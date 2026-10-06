# Stage 1: Binary build
FROM golang:1.22-alpine AS builder
WORKDIR /app
COPY go.mod main.go ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /temperature-api .

# Stage 2: Form image with executable
FROM alpine:3.22
COPY --from=builder /temperature-api /temperature-api
EXPOSE 8081
CMD ["/temperature-api"]
