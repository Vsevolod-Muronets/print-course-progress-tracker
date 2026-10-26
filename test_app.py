import pytest
from fastapi.testclient import TestClient

import app_final
from app_final import app

client = TestClient(app)

COURSE = "Дискретные и непрерывные структуры"
EXAM = {"name": "Экзамен", "points": 4, "count": 1}
HW = {"name": "ДЗ", "points": 4, "count": 2}
QUIZ = {"name": "Контрольные", "points": 2, "count": 2}
BONUS = {"name": "Допбалл", "points": 1, "count": 1}


@pytest.fixture(autouse=True)
def clean_state():
    app_final.courses.clear()
    app_final.tasks.clear()
    app_final.duties.clear()


def create_course(*components):
    return client.post("/courses", json={"name": COURSE, "components": list(components)})


def add_task(component="ДЗ", title="ДЗ 1"):
    return client.post(
        f"/courses/{COURSE}/tasks",
        json={"component": component, "title": title, "due": "2026-10-29"}
        )


def done(task_id, score):
    return client.post(f"/courses/{COURSE}/tasks/{task_id}/done", params={"score": score})


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_version():
    assert client.get("/version").json() == {"version": app_final.VERSION}


def test_duplicate_course():
    assert create_course(EXAM, HW, QUIZ).status_code == 201
    assert create_course(EXAM, HW, QUIZ).status_code == 409


def test_task_weight_and_contribution():
    create_course(EXAM, HW, QUIZ)
    add_task()
    result = done(1, 8).json()
    task = result["components"][1]["tasks"][0]
    assert task["weight"] == 2
    assert task["contribution"] == 1.6
    assert result["accumulated"] == 1.6


def test_close_component_recalculates_weight():
    create_course(QUIZ)
    add_task("Контрольные", "КР 1")
    assert done(1, 10).json()["accumulated"] == 1
    response = client.post(f"/courses/{COURSE}/close", params={"component": "Контрольные"})
    assert response.json()["accumulated"] == 2


def test_more_tasks_than_expected_recalculates_weight():
    create_course(HW)
    for number in range(1, 5):
        add_task(title=f"ДЗ {number}")
    assert done(1, 10).json()["accumulated"] == 1


def test_bonus_exceeds_ten_but_grade_is_capped():
    create_course(EXAM, HW, QUIZ, BONUS)
    tasks = [
        ("Экзамен", "Экзамен"),
        ("ДЗ", "ДЗ 1"),
        ("ДЗ", "ДЗ 2"),
        ("Контрольные", "КР 1"),
        ("Контрольные", "КР 2"),
        ("Допбалл", "Допзадание")
        ]
    for task_id, (component, title) in enumerate(tasks, 1):
        add_task(component, title)
        result = done(task_id, 10).json()
    assert result["accumulated"] == 11
    assert result["grade"] == 10


def test_score_out_of_range():
    create_course(HW)
    add_task()
    assert done(1, 11).status_code == 422


def test_unknown_course():
    assert client.get("/courses/нет").status_code == 404


def test_admin_duty():
    client.post("/admin", json={"title": "Пройти оценку преподавания", "due": "2026-12-25"})
    client.post("/admin/1/done")
    assert client.get("/admin").json()[0]["done"] is True
