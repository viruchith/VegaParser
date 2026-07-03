CREATE TABLE employees (
    id NUMBER PRIMARY KEY,
    name VARCHAR2(100)
);

CREATE OR REPLACE PACKAGE employee_pkg AS
    PROCEDURE hire_emp(p_name VARCHAR2);
END employee_pkg;
/

CREATE OR REPLACE PROCEDURE get_salary(p_id NUMBER) AS
BEGIN
    EXECUTE IMMEDIATE 'SELECT salary FROM employees WHERE id = :1';
END;
/
