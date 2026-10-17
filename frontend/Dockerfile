# syntax=docker/dockerfile:1
ARG NODE_IMAGE=node:20-alpine
# build
FROM ${NODE_IMAGE} AS build
ARG CA_BUNDLE_REVISION=none
RUN --mount=type=secret,id=corporate_ca,required=false \
    test -n "${CA_BUNDLE_REVISION}" && \
    mkdir -p /usr/local/share/ca-certificates && \
    if [ -s /run/secrets/corporate_ca ]; then \
      test "${CA_BUNDLE_REVISION}" != none && \
      cp /run/secrets/corporate_ca /usr/local/share/ca-certificates/corporate.crt && \
      chmod 0644 /usr/local/share/ca-certificates/corporate.crt; \
    else touch /usr/local/share/ca-certificates/corporate.crt; fi && \
    chmod 0644 /usr/local/share/ca-certificates/corporate.crt
ENV NODE_EXTRA_CA_CERTS=/usr/local/share/ca-certificates/corporate.crt
ARG OKF_BUILD_REVISION=unknown
ENV NEXT_PUBLIC_APP_REVISION=${OKF_BUILD_REVISION}
# Next freezes rewrites() during build. Match the runtime Compose endpoint so
# the published /health rewrite reaches the backend service after deployment.
ENV BACKEND_URL=http://backend:8000
WORKDIR /app
COPY package.json package-lock.json ./
COPY tooling/eslint-root-glob ./tooling/eslint-root-glob
RUN --mount=type=secret,id=npmrc,target=/root/.npmrc,required=false npm ci
COPY . .
RUN npm run build

# run
FROM ${NODE_IMAGE} AS runner
ARG CA_BUNDLE_REVISION=none
# Partition the runner cache too, and verify the public CA copied across stages.
RUN test -n "${CA_BUNDLE_REVISION}" && mkdir -p /usr/local/share/ca-certificates
COPY --from=build --chmod=0644 /usr/local/share/ca-certificates/corporate.crt /usr/local/share/ca-certificates/corporate.crt
RUN if [ "${CA_BUNDLE_REVISION}" = none ]; then \
      test ! -s /usr/local/share/ca-certificates/corporate.crt; \
    else \
      printf '%s  %s\n' "${CA_BUNDLE_REVISION}" /usr/local/share/ca-certificates/corporate.crt | sha256sum -c -; \
    fi
ENV NODE_EXTRA_CA_CERTS=/usr/local/share/ca-certificates/corporate.crt
ARG OKF_BUILD_REVISION=unknown
WORKDIR /app
ENV NODE_ENV=production
ENV HOSTNAME=0.0.0.0
ENV PORT=3000
ENV OKF_BUILD_REVISION=${OKF_BUILD_REVISION}

# node:*-alpine уже содержит непривилегированного пользователя node (uid 1000) —
# сервер Next не нуждается в root (порт 3000 > 1024). --chown нужен, чтобы
# standalone-вывод был доступен ему на чтение.
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
COPY --from=build --chown=node:node /app/public ./public
COPY --from=build --chown=node:node /app/scripts/diagnostics-runner.mjs ./diagnostics-runner.mjs
USER node

EXPOSE 3000
CMD ["node", "diagnostics-runner.mjs"]
