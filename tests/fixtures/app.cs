using System;
using System.Data;
using Npgsql;

namespace MyApp
{
    public class DatabaseService
    {
        private readonly string _connectionString;
        
        public DatabaseService(string host, int port, string database)
        {
            _connectionString = $"Host={host};Port={port};Database={database}";
        }
        
        public IDbConnection GetConnection()
        {
            return new NpgsqlConnection(_connectionString);
        }
        
        public static void Main(string[] args)
        {
            Console.WriteLine("Database service started");
        }
    }
}
