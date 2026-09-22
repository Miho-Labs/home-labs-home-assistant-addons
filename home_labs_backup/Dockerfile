ARG BUILD_FROM
FROM $BUILD_FROM

ENV LANG=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Build tools are only needed when no binary wheel exists for the target arch.
RUN apk add --no-cache --virtual .build-deps gcc musl-dev \
    && pip3 install --no-cache-dir "aiohttp==3.12.15" \
    && apk del .build-deps

COPY hl_backup /usr/src/hl_backup
# The HA integration this add-on installs into /config/custom_components.
COPY custom_components/home_labs_backup /usr/src/integration/home_labs_backup
COPY rootfs /

RUN chmod a+x /etc/services.d/hl-backup/run /etc/services.d/hl-backup/finish

ENV PYTHONPATH=/usr/src
WORKDIR /usr/src
