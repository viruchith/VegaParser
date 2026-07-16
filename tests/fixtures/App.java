import java.sql.Connection;
import java.sql.DriverManager;

public class App {
    private static final String DB_URL = "jdbc:postgresql://localhost:5432/mydb";
    
    public static void main(String[] args) throws Exception {
        System.out.println("Hello World");
    }
    
    public Connection getConnection() throws Exception {
        return DriverManager.getConnection(DB_URL, "user", "password");
    }
}
