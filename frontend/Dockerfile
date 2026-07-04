FROM node:20-bookworm-slim AS build

WORKDIR /app

ENV npm_config_audit=false \
    npm_config_fund=false \
    npm_config_update_notifier=false \
    npm_config_registry=https://registry.npmjs.org/

COPY package.json package-lock.json tsconfig.json vite.config.ts index.html ./
COPY src ./src

RUN rm -f package-lock.json \
    && npm install --no-audit --no-fund --loglevel=warn \
    && npm run build


FROM nginx:1.27-alpine

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
