#!/bin/bash

DB_HOST="postgres.internal.corp"
DB_PORT=5432
DB_NAME="myapp"

function connect_db() {
  local host=$1
  local port=$2
  psql -h "$host" -p "$port" -U admin -d "$DB_NAME"
}

function check_health() {
  curl -s https://health.example.com/status
  connect_db "$DB_HOST" "$DB_PORT"
}

check_health
