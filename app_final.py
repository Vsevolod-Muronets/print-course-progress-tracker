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
STUDY_YEAR = os.getenv("STUDY_YEAR", "1")
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


class NewStudent(BaseModel):
    name: str


class Student(BaseModel):
    courses: dict[str, Course] = {}
    tasks: dict[str, list[Task]] = {}
    duties: list[Duty] = []


students: dict[str, Student] = {}


def save():
    data = {"students": {name: s.model_dump(mode="json") for name, s in students.items()}}
    Path(DATA_FILE).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load():
    path = Path(DATA_FILE)
    text = path.read_text(encoding="utf-8").strip() if path.exists() else ""
    if not text:
        logger.info("Файл данных %s не найден или пуст, начинаем с пустых данных", DATA_FILE)
        return
    data = json.loads(text)
    students.update({name: Student(**s) for name, s in data.get("students", {}).items()})
    logger.info("Данные загружены из %s: студентов %d", DATA_FILE, len(students))


load()


def find_student(name: str) -> Student:
    if name not in students:
        raise HTTPException(404, "Студент не найден")
    return students[name]


def find_course(owner: Student, name: str) -> Course:
    if name not in owner.courses:
        raise HTTPException(404, "Курс не найден")
    return owner.courses[name]


def find_component(course: Course, name: str) -> Component:
    for component in course.components:
        if component.name == name:
            return component
    raise HTTPException(404, "Компонент не найден")


def progress(owner: Student, course: Course) -> dict:
    accumulated = 0
    components = []
    for component in course.components:
        items = [t for t in owner.tasks[course.name] if t.component == component.name]
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
    return {"message": "Сервис работает", "program": PROGRAM_NAME, "study_year": STUDY_YEAR}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/version")
def version():
    return {"version": VERSION}


@app.post("/students", status_code=201)
def create_student(new: NewStudent):
    if new.name in students:
        raise HTTPException(409, "Студент уже существует")
    students[new.name] = Student()
    save()
    logger.info("Создан студент %s", new.name)
    return new


@app.get("/students")
def list_students():
    return list(students)


@app.post("/students/{student}/courses", status_code=201)
def create_course(student: str, course: Course):
    owner = find_student(student)
    if course.name in owner.courses:
        raise HTTPException(409, "Курс уже существует")
    if len({c.name for c in course.components}) != len(course.components):
        raise HTTPException(422, "Названия компонентов должны быть уникальны")
    owner.courses[course.name] = course
    owner.tasks[course.name] = []
    save()
    logger.info("Студент %s: создан курс %s, компонентов: %d", student, course.name, len(course.components))
    return progress(owner, course)


@app.get("/students/{student}/courses")
def list_courses(student: str):
    owner = find_student(student)
    return [progress(owner, course) for course in owner.courses.values()]


@app.get("/students/{student}/courses/{name}")
def get_course(student: str, name: str):
    owner = find_student(student)
    return progress(owner, find_course(owner, name))


@app.post("/students/{student}/courses/{name}/tasks", status_code=201)
def add_task(student: str, name: str, new: NewTask):
    owner = find_student(student)
    course = find_course(owner, name)
    find_component(course, new.component)
    task = Task(id=len(owner.tasks[name]) + 1, **new.model_dump())
    owner.tasks[name].append(task)
    save()
    logger.info("Студент %s, курс %s: добавлено задание %s (%s) до %s", student, name, task.title, task.component, task.due)
    return progress(owner, course)


@app.post("/students/{student}/courses/{name}/tasks/{task_id}/done")
def finish_task(student: str, name: str, task_id: int, score: float = Query(ge=0, le=MAX_GRADE)):
    owner = find_student(student)
    course = find_course(owner, name)
    task = next((t for t in owner.tasks[name] if t.id == task_id), None)
    if task is None:
        raise HTTPException(404, "Задание не найдено")
    task.score = score
    save()
    result = progress(owner, course)
    logger.info("Студент %s, курс %s: задание %s выполнено на %s, накоп %s", student, name, task.title, score, result["accumulated"])
    return result


@app.post("/students/{student}/courses/{name}/close")
def close_component(student: str, name: str, component: str):
    owner = find_student(student)
    course = find_course(owner, name)
    find_component(course, component).closed = True
    save()
    logger.info("Студент %s, курс %s: компонент %s закрыт, новых заданий не будет", student, name, component)
    return progress(owner, course)


@app.post("/students/{student}/admin", status_code=201)
def add_duty(student: str, new: NewDuty):
    owner = find_student(student)
    duty = Duty(id=len(owner.duties) + 1, **new.model_dump())
    owner.duties.append(duty)
    save()
    logger.info("Студент %s: добавлена обязанность %s до %s", student, duty.title, duty.due)
    return duty


@app.get("/students/{student}/admin")
def list_duties(student: str):
    return find_student(student).duties


@app.post("/students/{student}/admin/{duty_id}/done")
def finish_duty(student: str, duty_id: int):
    owner = find_student(student)
    duty = next((d for d in owner.duties if d.id == duty_id), None)
    if duty is None:
        raise HTTPException(404, "Обязанность не найдена")
    duty.done = True
    save()
    logger.info("Студент %s: обязанность %s выполнена", student, duty.title)
    return duty