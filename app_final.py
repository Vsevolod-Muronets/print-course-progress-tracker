import json
import logging
import os
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

VERSION = "0.1.0"
MAX_GRADE = 10
PROGRAM_NAME = os.getenv("PROGRAM_NAME", "Программа обучения")
STUDENT_NAME = os.getenv("STUDENT_NAME", "Иванов Иван Иванович")
DATA_FILE = os.getenv("DATA_FILE", "data.json")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("tracker")

app = FastAPI(title=f"Трекер прогресса по курсам: {PROGRAM_NAME}", version=VERSION)

logger.info("Запуск: %s v%s", app.title, VERSION)


class Component(BaseModel):
    name: str
    points: float = Field(gt=0)
    count: int = Field(ge=1)
    closed: bool = False


class Course(BaseModel):
    name: str
    components: list[Component] = Field(min_length=1)


class NewTask(BaseModel):
    component: str
    title: str
    due: date


class Task(NewTask):
    id: int
    score: float | None = None


class NewDuty(BaseModel):
    title: str
    due: date


class Duty(NewDuty):
    id: int
    done: bool = False


courses: dict[str, Course] = {}
tasks: dict[str, list[Task]] = {}
duties: list[Duty] = []


def save():
    data = {
        "courses": {name: course.model_dump(mode="json") for name, course in courses.items()},
        "tasks": {name: [t.model_dump(mode="json") for t in items] for name, items in tasks.items()},
        "duties": [d.model_dump(mode="json") for d in duties]
        }
    Path(DATA_FILE).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load():
    path = Path(DATA_FILE)
    text = path.read_text(encoding="utf-8").strip() if path.exists() else ""
    if not text:
        logger.info("Файл данных %s не найден или пуст, начинаем с пустых данных", DATA_FILE)
        return
    data = json.loads(text)
    courses.update({name: Course(**course) for name, course in data.get("courses", {}).items()})
    tasks.update({name: [Task(**t) for t in items] for name, items in data.get("tasks", {}).items()})
    duties.extend(Duty(**d) for d in data.get("duties", []))
    logger.info("Данные загружены из %s: курсов %d, обязанностей %d", DATA_FILE, len(courses), len(duties))


load()


def find_course(name: str) -> Course:
    if name not in courses:
        raise HTTPException(404, "Курс не найден")
    return courses[name]


def find_component(course: Course, name: str) -> Component:
    for component in course.components:
        if component.name == name:
            return component
    raise HTTPException(404, "Компонент не найден")


def progress(course: Course) -> dict:
    accumulated = 0
    components = []
    for component in course.components:
        items = [t for t in tasks[course.name] if t.component == component.name]
        weight = component.points / max(len(items), 1 if component.closed else component.count)
        rows = []
        for task in items:
            contribution = weight * (task.score or 0) / MAX_GRADE
            accumulated += contribution
            rows.append({**task.model_dump(), "weight": round(weight, 2), "contribution": round(contribution, 2)})
        components.append({**component.model_dump(), "tasks": rows})
    accumulated = round(accumulated, 2)
    return {
        "course": course.name,
        "accumulated": accumulated,
        "grade": min(MAX_GRADE, accumulated),
        "components": components
        }


@app.get("/")
def root():
    return {"message": "Сервис работает", "program": PROGRAM_NAME, "student": STUDENT_NAME}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/version")
def version():
    return {"version": VERSION}


@app.post("/courses", status_code=201)
def create_course(course: Course):
    if course.name in courses:
        raise HTTPException(409, "Курс уже существует")
    if len({c.name for c in course.components}) != len(course.components):
        raise HTTPException(422, "Названия компонентов должны быть уникальны")
    courses[course.name] = course
    tasks[course.name] = []
    save()
    logger.info("Создан курс %s, компонентов: %d", course.name, len(course.components))
    return progress(course)


@app.get("/courses")
def list_courses():
    return [progress(course) for course in courses.values()]


@app.get("/courses/{name}")
def get_course(name: str):
    return progress(find_course(name))


@app.post("/courses/{name}/tasks", status_code=201)
def add_task(name: str, new: NewTask):
    course = find_course(name)
    find_component(course, new.component)
    task = Task(id=len(tasks[name]) + 1, **new.model_dump())
    tasks[name].append(task)
    save()
    logger.info("Курс %s: добавлено задание %s (%s) до %s", name, task.title, task.component, task.due)
    return progress(course)


@app.post("/courses/{name}/tasks/{task_id}/done")
def finish_task(name: str, task_id: int, score: float = Query(ge=0, le=MAX_GRADE)):
    course = find_course(name)
    task = next((t for t in tasks[name] if t.id == task_id), None)
    if task is None:
        raise HTTPException(404, "Задание не найдено")
    task.score = score
    save()
    result = progress(course)
    logger.info("Курс %s: задание %s выполнено на %s, накоп %s", name, task.title, score, result["accumulated"])
    return result


@app.post("/courses/{name}/close")
def close_component(name: str, component: str):
    course = find_course(name)
    find_component(course, component).closed = True
    save()
    logger.info("Курс %s: компонент %s закрыт, новых заданий не будет", name, component)
    return progress(course)


@app.post("/admin", status_code=201)
def add_duty(new: NewDuty):
    duty = Duty(id=len(duties) + 1, **new.model_dump())
    duties.append(duty)
    save()
    logger.info("Добавлена обязанность %s до %s", duty.title, duty.due)
    return duty


@app.get("/admin")
def list_duties():
    return duties


@app.post("/admin/{duty_id}/done")
def finish_duty(duty_id: int):
    duty = next((d for d in duties if d.id == duty_id), None)
    if duty is None:
        raise HTTPException(404, "Обязанность не найдена")
    duty.done = True
    save()
    logger.info("Обязанность %s выполнена", duty.title)
    return duty