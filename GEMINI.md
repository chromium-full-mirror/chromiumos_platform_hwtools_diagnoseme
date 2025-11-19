# Diagnose Me - Project Overview

This project contains the frontend and backend services for the Diagnose Me application.

## Directory Structure

-   `.`: Configuration files (linters, git, etc.)
-   `development_environment`: Docker compose and scripts for the development environment.
-   `dockerfiles`: Dockerfiles for building the various service images.
-   `src/common/protos`: Protocol buffer definitions for gRPC services.
-   `src/server`: Python gRPC server implementation.
-   `src/ui`: Angular frontend application.
-   `tests`: Unit tests.

## Common Commands

-   **Build all services:**
    ```bash
    ./development_environment/build-diagnoseme.sh
    ```
-   **Start services (Detached):**
    ```bash
    docker compose -f development_environment/docker-compose.yaml up -d
    ```
-   **Stop services:**
    ```bash
    docker compose -f development_environment/docker-compose.yaml down
    ```
-   **Run UI linters:**
    ```bash
    cd src/ui && npx gts lint
    ```
-   **Run UI tests:**
    ```bash
    cd src/ui && npm test
    ```
-   **Regenerate gRPC code (from `src/ui` directory):**
    ```bash
    npm run proto:generate
    ```

## Notes for AI Assistant

-   The backend is Python with gRPC.
-   The frontend is Angular.
-   Proto files are in `src/common/protos`.
-   Generated gRPC/TS code goes into `src/ui/src/proto`.
-   The main build process is orchestrated by `./development_environment/build-diagnoseme.sh`.
-   Pre-commit hooks are active, so ensure changes are linted and formatted.
-   The UI build process is sensitive to file name changes. Ensure that all references are updated in component decorators (templateUrl, styleUrl) and imports across modules and spec files when renaming component files.
-   The copyright header format is strict and requires a blank line after the closing `-->` in HTML files.
-   Never use the --no-verify argument to git commit.
-   When renaming Angular components, pay close attention to:
    -   Directory names
    -   File names (e.g., `*.html`, `*.scss`, `*.ts`, `*.spec.ts`)
    -   Class names within `.ts` files
    -   Selectors in `@Component` decorators
    -   `templateUrl` and `styleUrl` paths in `@Component` decorators
    -   Import paths in other components and modules
    -   Component declarations in `app.module.ts`
