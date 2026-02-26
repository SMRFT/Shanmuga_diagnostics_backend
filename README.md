# Shanmuga Diagnostics LIS Backend

This project serves as the backend API layer for the Laboratory Information System (LIS) developed for Shanmuga Diagnostics.

## Project Architecture

The backend is built using Django and Django REST Framework (DRF), and is responsible for managing laboratory data, user permissions, finance records, and real-time operations across branches.

### Data Storage

*   **Primary Database:** The system connects with Django's default configured relational database (for core user management, patients, and standard settings).
*   **Secondary Document Store:** MongoDB (`pymongo`) is used extensively for flexible schema requirements like Invoice tracking, complex payment histories, dynamic test allocations, and report metadata.

## Core API Endpoints & Functionality

The `core` application handles several primary operations:

*   **Patient Registration & Lookup:** Creating new patients with associated IDs and parsing test detail information.
*   **Laboratory Testing:** Recording findings and results for sample analysis.
*   **Billing & B2B Invoicing:** Managing segment-wise operations (B2C & B2B), tracking due credits, calculating proportions upon payment updates, and storing historical payments in NoSQL.
*   **Role & Data Permissions:** The backend features a robust permission map mechanism enforcing `HasRoleAndDataPermission` logic based on user authentication tokens and `Branch-Code` headers.

## Local Development

The project is structured as a standard Django application.

### Starting the Server

```bash
# Navigate to the repository
cd Shanmuga_diagnostics_backend

# Install required packages (if a virtual environment is used)
pip install -r requirements.txt

# Run the development server
python3 manage.py runserver
```

A `.env` file containing configuration keys (such as `GLOBAL_DB_HOST` and `APP_URL`) is required for integration points such as PyMongo connections and email sending.

## Recent Updates
*   **Invoice Proportion Fixes:** Updated backend B2B logic to mathematically zero-out due credits for fully-paid patients, and correctly allocate proportions when dealing with pending values across MongoDB and Relational databases simultaneously.
*   **Pharmacy Billing Support:** Additional endpoints integrated to support fetching and registering prescribed medical items over the DRF serializers.
