CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(50) NOT NULL UNIQUE,
    email VARCHAR(100) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE orders (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id),
    total DECIMAL(10,2),
    status VARCHAR(20)
);

CREATE OR REPLACE PROCEDURE create_user(
    p_username VARCHAR,
    p_email VARCHAR
)
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO users (username, email) VALUES (p_username, p_email);
END;
$$;
