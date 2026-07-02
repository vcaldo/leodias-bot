FROM golang:1.26-alpine AS build

WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY *.go ./
RUN CGO_ENABLED=0 go build -ldflags="-s -w" -o /leodias-bot .

FROM alpine:3.21

RUN apk add --no-cache ca-certificates \
    && adduser -D -u 10001 bot
USER bot

COPY --from=build /leodias-bot /usr/local/bin/leodias-bot

ENTRYPOINT ["leodias-bot"]
