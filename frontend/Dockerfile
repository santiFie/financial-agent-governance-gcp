# ==============================================================================
# STAGE 1: Build React App with Vite
# ==============================================================================
FROM node:20-alpine AS builder

WORKDIR /app

COPY package.json ./
RUN npm install

COPY . .
RUN npm run build

# ==============================================================================
# STAGE 2: Serve with NGINX (Ultra-lightweight static delivery)
# ==============================================================================
# Unprivileged variant: runs as a non-root user and listens on 8080.
FROM nginxinc/nginx-unprivileged:alpine AS runner

# Actualizar paquete vulnerable CVE-2026-93990 (needs root, then drop back to the nginx user)
USER root
RUN apk update && apk add --no-cache --upgrade expat
USER 101

COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -q --spider http://127.0.0.1:8080/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
