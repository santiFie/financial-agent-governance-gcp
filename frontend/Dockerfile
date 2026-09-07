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
FROM nginx:alpine AS runner

COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
