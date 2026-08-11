#!/usr/bin/env python3
"""Builds binary/generated fixture files that shouldn't be hand-typed as text
(currently just the EdTech SQLite fixture). Re-run whenever the fixture shape
needs to change. Safe to run repeatedly — it recreates the file each time.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "fixtures" / "datasets" / "edtech.sqlite"

STUDENTS = [
    (1, "Ishaan Gupta", "ishaan.g@example.com", "2023-08-01", "10th Grade"),
    (2, "Aarohi Patel", "aarohi.p@example.com", "2023-08-01", "10th Grade"),
    (3, "Vivaan Reddy", "vivaan.r@example.com", "2023-08-15", "11th Grade"),
    (4, "Myra Desai", "myra.d@example.com", "2023-09-01", "9th Grade"),
    (5, "Kabir Menon", "kabir.m@example.com", "2023-09-10", "11th Grade"),
    (6, "Anaya Pillai", "anaya.p@example.com", "2023-10-02", "10th Grade"),
]

COURSES = [
    (101, "Algebra II", "Mathematics", 4999.0),
    (102, "Physics Foundations", "Science", 5999.0),
    (103, "Creative Writing", "Language Arts", 3499.0),
    (104, "Intro to Python", "Computer Science", 6999.0),
]

ENROLLMENTS = [
    (1, 1, 101, "2023-08-05", "active", 82.5),
    (2, 1, 104, "2023-09-01", "active", 91.0),
    (3, 2, 101, "2023-08-05", "completed", 76.0),
    (4, 3, 102, "2023-08-20", "active", 88.0),
    (5, 3, 104, "2023-09-15", "dropped", None),
    (6, 4, 103, "2023-09-05", "active", 95.5),
    (7, 5, 102, "2023-09-12", "completed", 79.0),
    (8, 5, 104, "2023-10-01", "active", 84.0),
    (9, 6, 101, "2023-10-05", "active", 67.5),
    (10, 6, 103, "2023-10-10", "active", 90.0),
]


def main() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute(
        "CREATE TABLE students (student_id INTEGER PRIMARY KEY, full_name TEXT, "
        "email TEXT, enrolled_since TEXT, grade_level TEXT)"
    )
    cur.execute(
        "CREATE TABLE courses (course_id INTEGER PRIMARY KEY, course_name TEXT, "
        "subject TEXT, price_inr REAL)"
    )
    cur.execute(
        "CREATE TABLE enrollments (enrollment_id INTEGER PRIMARY KEY, student_id INTEGER, "
        "course_id INTEGER, enrolled_on TEXT, status TEXT, score REAL, "
        "FOREIGN KEY(student_id) REFERENCES students(student_id), "
        "FOREIGN KEY(course_id) REFERENCES courses(course_id))"
    )
    cur.executemany("INSERT INTO students VALUES (?, ?, ?, ?, ?)", STUDENTS)
    cur.executemany("INSERT INTO courses VALUES (?, ?, ?, ?)", COURSES)
    cur.executemany("INSERT INTO enrollments VALUES (?, ?, ?, ?, ?, ?)", ENROLLMENTS)
    con.commit()
    con.close()
    print(f"wrote {DB_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
