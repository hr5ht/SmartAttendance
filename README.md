# Smart Attendance System

A classroom attendance application built with **Django, SQLite, and face recognition**. Teachers upload classroom photos, review detected students, and confirm attendance for a section, subject, date, and period. Students complete their own guided face enrollment from a phone.

The interface uses server-rendered Django templates, custom dark-mode CSS, and vanilla JavaScript. Recognition runs on the CPU through InsightFace and ONNX Runtime.

## Contents

- [Features](#features)
- [Technology stack](#technology-stack)
- [Prerequisites](#prerequisites)
- [Getting started](#getting-started)
- [First-time administration](#first-time-administration)
- [Attendance workflow](#attendance-workflow)
- [Configuration](#configuration)
- [Project structure](#project-structure)
- [Development checks](#development-checks)
- [Troubleshooting](#troubleshooting)
- [Production configuration](#production-configuration)

## Features

| Role | Capabilities |
| --- | --- |
| **Admin** | Manage students, issue credentials, reset passwords, review enrollment flags and reset requests, and configure academic records. |
| **Teacher** | Select class details, view the day's timetable and sessions, upload photos, review matches, correct attendance, and confirm a register. |
| **Student** | Change an initial password, complete five guided face captures, request an enrollment reset, and view personal attendance. |

- Branch, year, section, subject, and period selection with timetable-based defaults.
- Phone-friendly enrollment with camera capture, previews, retakes, and a file-upload fallback.
- Server-side checks for face count, confidence, size, lighting, blur, duplicate samples, and possible cross-student identity conflicts.
- Recognition against **only face-enrolled students in the selected section**.
- Bounding-box overlays, unknown-face review, and manual attendance corrections.
- Draft sessions that become final only after teacher confirmation.
- Plain-text confirmation emails; delivery failures do not undo saved attendance.
- Attendance percentages, reports, and CSV export.
- Dark interface with responsive layouts, loading indicators, and lightweight animations.

## Technology stack

| Layer | Technology |
| --- | --- |
| Language | Python 3.11 |
| Web application | Django 5.x and server-rendered templates |
| Frontend | HTML, handwritten CSS, vanilla JavaScript |
| Database | SQLite; embeddings stored as float32 BLOBs |
| Detection and recognition | InsightFace `buffalo_l`: SCRFD detector and ArcFace embeddings |
| Inference | ONNX Runtime with the CPU execution provider |
| Matching | NumPy cosine similarity and matrix multiplication |
| Image handling | Pillow and OpenCV |
| Email | Django email backend with SMTP configuration |
| Development tests | pytest and pytest-django |

Direct versions are pinned in [requirements.txt](requirements.txt). There is no frontend build step, Redis, Celery, or external database service. Image processing is synchronous, with browser requests displaying progress.

## Prerequisites

- **Python 3.11** with `pip` and `venv` support.
- **Git** to clone the repository.
- Internet access for dependencies and the initial model download.
- Disk space for the Python environment, model files, SQLite database, and uploaded photos. The default model download is approximately 280 MB; extracted files and dependencies require additional space.
- A modern browser. A camera is useful for enrollment, with file upload available as a fallback.
- SMTP credentials if actual email delivery is required outside development.

A GPU is not required. Some platforms may need native compiler/build tools when a dependency cannot be installed from a prebuilt wheel.

## Getting started

### 1. Clone the repository

Replace `<repository-url>` with this repository's GitHub clone URL:

```sh
git clone <repository-url> SmartAttendance
cd SmartAttendance
```

### 2. Run automated setup

Install Python 3.11, clone the repository, then run from the project directory:

```sh
python3.11 setup_project.py
```

On Windows, use `py -3.11 setup_project.py`.

The script creates `.venv`, installs `requirements.txt`, checks Django settings,
downloads and loads the configured face models, applies migrations, and collects
static assets. Internet access is required for packages and the initial model
download. Package installation may require platform-specific build tools; if a
step fails, setup stops and reports the error.

When `.env` is absent, setup creates it from `.env.example` with a random Django
secret key and development defaults. Existing `.env` files are preserved. To
choose a different model or configure production settings, create and edit `.env`
before running setup. Process environment variables take precedence over `.env`.

Setup can be rerun: existing model files are reused, and only pending migrations
are applied. It never creates demo students or enrollments.

### 3. Create an administrator and start locally

```sh
.venv/bin/python manage.py createsuperuser
.venv/bin/python manage.py runserver
```

On Windows, replace `.venv/bin/python` with `.venv\Scripts\python.exe`.

Open **http://127.0.0.1:8000/** and sign in with the administrator credentials you created.

### Manual installation alternative

Create a virtual environment using Python 3.11:

```sh
python3.11 -m venv .venv
source .venv/bin/activate
```

On Windows PowerShell, use:

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
```

With the environment activated, install the packages:

```sh
python -m pip install -r requirements.txt
```

Copy `.env.example` to `.env` and replace `DJANGO_SECRET_KEY=change-me` with a private value. Generate a key with:

```sh
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

Then prepare and start the application:

```sh
python manage.py check
python manage.py download_face_models
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py createsuperuser
python manage.py runserver
```

## First-time administration

A fresh database starts without classes, teachers, or students. Configure them in this order:

1. Open `/admin/` and create departments such as **CSE, IT, and ECE**.
2. Add sections with a department, year **1–4**, and section **A, B, or C**. Section names are generated from those fields.
3. Create subjects for the departments.
4. Create users with the **TEACHER** role, then their linked Teacher profiles. Enter teacher email addresses for summaries.
5. Add Teacher Assignments linking teachers to sections and subjects, followed by Timetable Slots for weekdays and periods.
6. Open **Students → Add student** in the application to create student records and generate credentials. View, print, or export the credentials for distribution.
7. Ask students to sign in, change their initial passwords, and complete face enrollment.

Set `RESTRICT_TEACHER_TO_ASSIGNMENTS=1` for assignment-based class selection. The supplied `.env.example` currently uses `0`, allowing selection of any active class while teachers still access their own sessions.

| Page | Path |
| --- | --- |
| Sign in | `/accounts/login/` |
| Admin dashboard | `/accounts/dashboard/admin/` |
| Student management | `/manage/students/` |
| Academic data administration | `/admin/` |
| Teacher dashboard | `/attendance/` |
| Student face enrollment | `/student/enroll/` |
| Reports | `/reports/` |
| Student's personal attendance | `/reports/me/` |

Access depends on the signed-in user's role.

## Attendance workflow

### Student enrollment

Students submit five captures at `/student/enroll/`: straight ahead, slightly left, slightly right, slightly upward, and another straight-on image with different lighting or background.

Each capture is validated on the server. Poor-quality and near-duplicate samples require a retake. Suspected matches to another enrolled student in the same section are flagged for review. Enrollment is complete only after all required captures pass.

### Teacher session

1. Select branch, year, section, subject, period, and date on the teacher dashboard.
2. Upload classroom photos and create a draft. The session page starts processing pending images and displays progress.
3. Review face labels, unknown faces, and the full roster. Students without completed enrollment are listed separately.
4. Correct attendance and assign unknown faces where appropriate.
5. Confirm the register. The application saves attendance and attempts to email the summary.

The daily dashboard lists sessions and their status alongside the selected day's timetable.

### Recognition rules

The pipeline builds a gallery from the selected section's enrolled students and scores detected faces against its embeddings using NumPy. A match must pass both the similarity threshold and the margin over the next-best **different student**.

If two faces in one photo claim the same student, only the higher-scoring match is retained. Results are combined across photos: a student matched in any image is present, with the best confidence retained. Unaccepted matches remain unknown for teacher review.

## Configuration

Settings are defined in [config/settings.py](config/settings.py), with examples in [.env.example](.env.example). Process environment variables take precedence over `.env` values.

| Variable | Purpose |
| --- | --- |
| `DJANGO_SECRET_KEY` | Private Django signing key; generated by automated setup. |
| `DJANGO_DEBUG` | `1` for development; `0` for deployment. |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated allowed hostnames. |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Trusted origins including scheme, when required by deployment. |
| `DJANGO_TIME_ZONE` | Timezone; defaults to `Asia/Kolkata`. |
| `DJANGO_DB_PATH` | SQLite database location. |
| `DJANGO_MEDIA_ROOT` | Uploaded photo storage location. |
| `INSTITUTION_NAME` | Institution name displayed in the application. |
| `FACE_MODEL_NAME` | Model pack; defaults to `buffalo_l`. |
| `FACE_MODEL_ROOT` | Root directory for model downloads. |
| `FACE_DET_SIZE` | Detector input size; defaults to `640`. |
| `SIMILARITY_THRESHOLD` | Minimum cosine similarity; defaults to `0.45`. |
| `MARGIN_THRESHOLD` | Required lead over the runner-up student; defaults to `0.05`. |
| `MIN_ENROLL_FACE_PIXELS` | Minimum enrollment face width; defaults to `120`. |
| `DUPLICATE_EMBEDDING_THRESHOLD` | Near-duplicate enrollment cutoff; defaults to `0.98`. |
| `MAX_UPLOAD_SIZE_MB` | Maximum individual upload size; defaults to `12`. |
| `MAX_SESSION_IMAGES` | Maximum photos per session; defaults to `3`. |
| `RESTRICT_TEACHER_TO_ASSIGNMENTS` | Enable assignment-based class selection with `1`. |

### Model downloads

```sh
.venv/bin/python manage.py download_face_models
```

`FACE_MODEL_NAME` defaults to `buffalo_l`; `FACE_MODEL_ROOT` controls the download
location. InsightFace stores files under `<FACE_MODEL_ROOT>/models/<model-name>`.
Model weights are excluded from Git, but must be available to the application
on the server. Setup uses real models and stops if download or loading fails.

With the default configuration, the directory is `models/models/buffalo_l/`. The repeated `models` directory is expected.

### Email setup

Development uses the console backend by default: messages appear in the terminal without being delivered. To enable SMTP, configure these values in `.env`:

```dotenv
DJANGO_EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.example.com
EMAIL_PORT=587
EMAIL_HOST_USER=your-smtp-username
EMAIL_HOST_PASSWORD=your-smtp-password
EMAIL_USE_TLS=1
DEFAULT_FROM_EMAIL=Smart Attendance <no-reply@example.edu>
ATTENDANCE_EMAIL_CC=
```

Replace the example values with your provider's settings. Confirmation emails currently contain a plain-text roster summary. `ATTENDANCE_EMAIL_CC` accepts extra comma-separated recipients; the current implementation includes them in the message's `To` list. CSV export is provided through reports rather than as an email attachment.

## Project structure

```text
SmartAttendance/
├── apps/
│   ├── accounts/           # Users, roles, authentication, dashboards
│   ├── academics/          # Departments, sections, subjects, timetable
│   ├── students/           # Student records, credentials, reset administration
│   ├── attendance/         # Sessions, processing pipeline, overrides, email
│   ├── face/               # Model interface, validation, enrollment, matching
│   │   └── management/commands/download_face_models.py
│   └── reports/            # Attendance reports and CSV export
├── config/
│   ├── settings.py         # Environment-configurable Django settings
│   ├── env.py              # Dependency-free .env loader
│   ├── urls.py             # Root URL routing
│   ├── wsgi.py             # WSGI entry point
│   └── asgi.py             # ASGI entry point
├── templates/              # Shared layout and role-specific Django templates
├── static/
│   ├── css/                # Design system, dark theme, workflow styles
│   └── js/                 # Camera, session forms, processing progress, UI
├── .env.example            # Configuration template
├── .gitignore              # Local/runtime exclusions
├── manage.py               # Django management commands
├── requirements.txt        # Pinned Python dependencies
├── setup_project.py        # Automated setup
└── README.md
```

Database migrations are included in each application's `migrations/` directory. Local/generated paths include `.venv/`, `.env`, `db.sqlite3`, `media/`, `models/`, `logs/`, and `staticfiles/`; these are excluded from Git.

The current repository rules also exclude `tests/`, `pytest.ini`, and development seed commands. A fresh GitHub clone therefore does not include these development tools, even though local working copies may retain them.

## Development checks

With the virtual environment activated:

```sh
python manage.py check
python manage.py migrate --check
```

For local checkouts that retain the excluded test suite and `pytest.ini`, run `python -m pytest`. Tests cover matching, enrollment, permissions, dashboards, confirmation, and reports; model-backed tests require downloaded model files.

## Troubleshooting

| Problem | What to check |
| --- | --- |
| Package installation fails | Read the failed package's error. Confirm the Python version and install platform build tools if compilation is required, then rerun setup. |
| Model download or loading fails | Check internet access, disk space, and write permissions for `FACE_MODEL_ROOT`; rerun `download_face_models`. |
| No classes appear | Create academic records and a Teacher profile. If assignment restrictions are enabled, add Teacher Assignments. |
| No enrolled students in the section | Students must complete all enrollment captures before automatic recognition can work. |
| No faces detected or weak matches | Use clearer classroom photos with larger, visible faces and better lighting. Review unknown matches manually. |
| Camera permission denied | Allow camera access or use the file-upload fallback. Use localhost or HTTPS for live camera access. |
| Duplicate session | Open the existing session for that section, subject, period, and date. |
| Email appears only in the terminal | The console backend is active. Configure SMTP for delivery. |
| SMTP delivery fails | Check the teacher's email and SMTP settings. Confirmed attendance remains saved. |

Pipeline diagnostics are written to `logs/attendance.log` with processing counts and timing information.

## Production configuration

Setup prepares application files; it does not provision or start a production
web server. Configure `DJANGO_DEBUG=0`, a private `DJANGO_SECRET_KEY`, and explicit
`DJANGO_ALLOWED_HOSTS`. Use a production WSGI/ASGI server with HTTPS, serve the
collected `staticfiles/` assets, and keep the SQLite database and uploads on
persistent storage with backups. Do not replace live data with development data.

For SMTP, configure `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`,
`EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS`, and `DEFAULT_FROM_EMAIL` in the environment.
`ATTENDANCE_EMAIL_CC` accepts comma-separated addresses. Development defaults to
console email; no SMTP message is sent by the setup script.

Run `.venv/bin/python manage.py check --deploy` against production settings and
review its findings before serving traffic.
