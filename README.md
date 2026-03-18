# Framework for Requirements & Objectives Governance (FROG) — Requirements Manager

A desktop application for managing engineering requirements, built with Python and PySide6 (Qt6). Framework for Requirements & Objectives Governance (FROG) organises requirements into a structured hierarchy, tracks changes with a full audit trail, and supports multi-user access control.

## Features

### Hierarchical Requirements Management
- Organise work into **Projects > Systems > SubSystems > Elements > Requirements**
- Navigate the full hierarchy via an interactive tree view with search/filter
- Add, edit, delete, and reorder entities at any level with drag-and-drop
- Link any two entities together to capture cross-cutting relationships

### Rich Requirement Editing
- Dedicated fields for each requirement: name, body, priority, status, rationale, requirement ID
- Rich text editor with bold, italic, underline, bullet/numbered lists, and embedded images
- Attach test plan files and external ticket links (clickable from the detail view)

### AI-Powered Analysis (Local)
- Analyse requirement quality against best practices using a local Llama model (no data leaves your machine)
- Generate test templates from requirements with overwrite protection

### Export
- Export the full project to **PDF**, **Word (.docx)**, **plain text**, **CSV**, or **ReqIF** (XML interchange format)
- Rich text and embedded images are preserved in PDF and Word exports
- Export change history for a single entity or the entire project to CSV

### Document View
- Read-only formatted document view of the complete project hierarchy, generated on demand

### Link Graph (Experimental)
- Visualise all entity relationships as a force-directed graph
- Drag nodes to rearrange, scroll to zoom, double-click to inspect
- Colour-coded by entity type with a legend

### User Management and Access Control
- Multi-user authentication with login, account creation, and password reset
- **Administrator** has full access to everything and can:
  - Assign users as **Project Database Managers**
  - Grant any user access to any project
  - Relocate the database file
- **Project Database Managers** can grant/revoke member access for their assigned projects
- **Members** can access and edit projects they have been granted access to
- User search by name, username, or email when assigning roles

### Audit Trail
- Every create, update, delete, link, and unlink operation is logged with timestamp and user
- View the change history of any entity showing who changed what, with old and new values
- Export history to CSV for detailed inspection

### Theming
- Dark and light theme toggle (via qdarktheme)

## Getting Started

### Prerequisites

- Python 3.10+
- pip

### Installation

```bash
git clone https://github.com/gigidinut/reqman.git
cd reqman
pip install PySide6 pyqtdarktheme sqlalchemy werkzeug reportlab python-docx
```

### Running

```bash
python main.py
```

On first run, the database is created automatically and a default admin account is seeded:

| Field    | Value            |
|----------|------------------|
| Username | `admin`          |
| Password | `passwordtemp`   |

You will be prompted to change the password on first login.

## User Guide

### Logging In
Launch the app and log in with your credentials. New users can create an account from the login screen, or the administrator can create accounts and assign them to projects.

### Creating a Project
From the main menu, click **Create New Project**, enter a name and optional description. You are automatically assigned as the project manager.

### Working in a Project
After opening a project, the workspace has three areas:
- **Left panel** — the entity tree with search and action buttons
- **Centre panel** — detail view for the selected entity, or document view
- **Top bar** — toggle views, export, link graph, manage access, and export history

#### Adding Entities
Select a parent in the tree (or nothing for top-level), then click one of the add buttons: **+ System**, **+ SubSystem**, **+ Element**, or **+ Requirement**. Fill in the dialog and save.

#### Editing and Deleting
Select an entity and use the **Edit** or **Delete** buttons. Deleting a non-leaf entity removes all its descendants (with a confirmation warning).

#### Linking Entities
Open the edit dialog for any entity and use the **Linked Entities** section to search for and link other entities. Linked entities appear in the detail panel and are clickable.

#### Viewing History
Select an entity and click **History** to see all changes, who made them, and the old vs new values. Use **Export CSV** to save the history.

### Exporting
- **Export Project** (top bar) — choose PDF, Word, TXT, CSV, or ReqIF and save
- **Export History** (top bar) — exports the full project change log to CSV

### Managing Access
- **Admin**: click **Manage DB Managers** on the main menu to assign project managers
- **Admin or Project Manager**: click **Manage Access** in the project top bar to grant or revoke user access

### Logging Out
Click **Logout** in the top bar to return to the login screen.

## Project Structure

```
reqman/
  main.py                  # Application entry point
  database/
    models.py              # SQLAlchemy ORM models
  controllers/
    db_controllers.py      # Database operations and audit logging
    ai_controller.py       # Local AI model integration
    export_controller.py   # PDF, DOCX, TXT, CSV, ReqIF export
    config_controller.py   # User configuration persistence
    paths.py               # Writable path resolution
  views/
    auth_view.py           # Login / signup / password reset
    main_view.py           # Main menu, project dialogs, admin dialogs
    project_view.py        # Project workspace, tree, detail, history
    entity_dialogs.py      # Add/edit dialogs for non-requirement entities
    requirement_dialog.py  # Add/edit dialogs for requirements
    rich_text_editor.py    # Rich text editor widget
    link_graph_view.py     # Force-directed link graph visualisation
```

## License

This project is for personal/internal use. No license has been specified.
