require 'pg'
require 'json'

class DatabaseClient
  def initialize(host, port, dbname)
    @host = host
    @port = port
    @dbname = dbname
  end

  def connect
    PG.connect(host: @host, port: @port, dbname: @dbname)
  end

  def fetch_users(conn)
    conn.exec("SELECT * FROM users")
  end
end

def format_result(result)
  result.map { |row| row.to_h }
end
