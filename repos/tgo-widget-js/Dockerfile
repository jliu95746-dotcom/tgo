# TGO Widget App (Vite) - build and serve via Nginx

# --- Build stage ---
FROM node:20-alpine AS builder
WORKDIR /app
ENV CI=true

# Build-time envs for Vite (must be defined BEFORE build)
# Note: These are no longer used for API_BASE, which is now runtime-configurable
ARG VITE_API_BASE_URL
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}

COPY package.json yarn.lock ./
ARG NPM_REGISTRY=https://registry.npmjs.org
RUN yarn config set registry "$NPM_REGISTRY" && \
    node -e 'const fs=require("fs");const u=new URL(process.env.NPM_REGISTRY);if(u.protocol!=="https:"||u.username||u.password||u.search||u.hash)throw Error("Invalid registry");fs.writeFileSync("yarn.lock",fs.readFileSync("yarn.lock","utf8").replace(/https:\/\/registry\.(npmjs\.org|yarnpkg\.com)/g,u.href.replace(/\/$/,"")))'
RUN yarn install --frozen-lockfile

COPY . .
RUN echo "Building with VITE_API_BASE_URL=${VITE_API_BASE_URL}" && yarn build

# --- Runtime stage ---
FROM nginx:alpine
RUN apk add --no-cache curl

COPY --from=builder /app/dist /usr/share/nginx/html

# Copy entrypoint script
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN sed -i 's/\r$//' /docker-entrypoint.sh && chmod +x /docker-entrypoint.sh

# Copy nginx config
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 CMD curl -f http://localhost/ || exit 1

# Use entrypoint script to generate runtime config and start Nginx
ENTRYPOINT ["/docker-entrypoint.sh"]

