# build
FROM node:20-alpine AS build
ARG OKF_BUILD_REVISION=unknown
ENV NEXT_PUBLIC_APP_REVISION=${OKF_BUILD_REVISION}
# Next freezes rewrites() during build. Match the runtime Compose endpoint so
# the published /health rewrite reaches the backend service after deployment.
ENV BACKEND_URL=http://backend:8000
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

# run
FROM node:20-alpine AS runner
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
