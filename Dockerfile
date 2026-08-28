FROM mcr.microsoft.com/playwright/python:v1.62.0-noble

ARG APP_UID=1000
ARG APP_GID=1000

# Install Requirements
WORKDIR /app

# Disable Python output buffering for real-time logs
ENV PYTHONUNBUFFERED=1

# Ensure the application source directory is discoverable by Python imports
ENV PYTHONPATH=/app/src

COPY requirements.txt .
RUN pip install --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Keep image ownership configurable while reusing Playwright's non-root user.
RUN set -eux; \
    current_uid="$(id -u pwuser)"; \
    current_gid="$(id -g pwuser)"; \
    uid_owners="$(getent passwd "$APP_UID" | cut -d: -f1 || true)"; \
    gid_owners="$(getent group "$APP_GID" | cut -d: -f1 || true)"; \
    if [ -n "$uid_owners" ] && [ "$uid_owners" != "pwuser" ] && [ "$uid_owners" != "ubuntu" ]; then \
        echo "APP_UID $APP_UID is already assigned to unrelated user(s): $uid_owners" >&2; \
        exit 1; \
    fi; \
    if [ -n "$gid_owners" ] && [ "$gid_owners" != "pwuser" ] && [ "$gid_owners" != "ubuntu" ]; then \
        echo "APP_GID $APP_GID is already assigned to unrelated group(s): $gid_owners" >&2; \
        exit 1; \
    fi; \
    next_free_id() { \
        candidate=60000; \
        while getent passwd "$candidate" >/dev/null || getent group "$candidate" >/dev/null || \
              [ "$candidate" = "$APP_UID" ] || [ "$candidate" = "$APP_GID" ]; do \
            candidate=$((candidate + 1)); \
        done; \
        printf '%s' "$candidate"; \
    }; \
    if [ "$uid_owners" = "ubuntu" ]; then \
        temp_uid="$(next_free_id)"; \
        usermod --uid "$temp_uid" ubuntu; \
    fi; \
    if [ "$gid_owners" = "ubuntu" ]; then \
        ubuntu_primary_users="$(getent passwd | awk -F: -v target_gid="$APP_GID" '$4 == target_gid { print $1 }' || true)"; \
        if [ -n "$ubuntu_primary_users" ] && [ "$ubuntu_primary_users" != "ubuntu" ]; then \
            echo "APP_GID $APP_GID is the primary group for unrelated user(s): $ubuntu_primary_users" >&2; \
            exit 1; \
        fi; \
        temp_gid="$(next_free_id)"; \
        ubuntu_primary_gid="$(getent passwd ubuntu | cut -d: -f4 || true)"; \
        groupmod --gid "$temp_gid" ubuntu; \
        if [ "$ubuntu_primary_gid" = "$APP_GID" ]; then \
            usermod --gid "$temp_gid" ubuntu; \
        fi; \
    fi; \
    echo "Configuring pwuser from ${current_uid}:${current_gid} to ${APP_UID}:${APP_GID}"; \
    pwuser_group_gid="$(getent group pwuser | cut -d: -f3 || true)"; \
    if [ "$pwuser_group_gid" != "$APP_GID" ]; then \
        if getent group "$APP_GID" >/dev/null; then \
            echo "APP_GID $APP_GID remains assigned to another group" >&2; \
            exit 1; \
        fi; \
        groupmod --gid "$APP_GID" pwuser; \
    fi; \
    pwuser_uid="$(id -u pwuser)"; \
    if [ "$pwuser_uid" != "$APP_UID" ]; then \
        if getent passwd "$APP_UID" >/dev/null; then \
            echo "APP_UID $APP_UID remains assigned to another user" >&2; \
            exit 1; \
        fi; \
        usermod --uid "$APP_UID" pwuser; \
    fi; \
    usermod --gid "$APP_GID" pwuser; \
    chown -R "$APP_UID:$APP_GID" /home/pwuser; \
    final_uid="$(id -u pwuser)"; \
    final_gid="$(id -g pwuser)"; \
    [ "$final_uid" = "$APP_UID" ] || { echo "pwuser UID mismatch: $final_uid != $APP_UID" >&2; exit 1; }; \
    [ "$final_gid" = "$APP_GID" ] || { echo "pwuser GID mismatch: $final_gid != $APP_GID" >&2; exit 1; }

ENV HOME=/home/pwuser
USER pwuser

# Default Port 
EXPOSE 6969

# Run the application via the startup wrapper
CMD ["python", "src/run.py", "--host", "0.0.0.0", "--port", "6969"]
