# TaskerKeeper dashboard image (task 1.3.1 scaffold).
# Compose builds ./web and routes tk.example.com to port 3000.
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json ./
RUN npm install
COPY . .
RUN npm run build

FROM node:20-alpine
WORKDIR /app
ENV NODE_ENV=production
COPY --from=build /app/build ./build
COPY --from=build /app/package.json ./
RUN npm install --omit=dev
EXPOSE 3000
ENV PORT=3000
CMD ["node", "build"]
