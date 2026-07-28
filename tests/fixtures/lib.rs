use std::fmt;

pub struct Database {
    pub host: String,
    pub port: u16,
    pub name: String,
}

impl Database {
    pub fn new(host: &str, port: u16, name: &str) -> Self {
        Database {
            host: host.to_string(),
            port,
            name: name.to_string(),
        }
    }
    pub fn connection_string(&self) -> String {
        format!("postgres://{}:{}/{}", self.host, self.port, self.name)
    }
}

impl fmt::Display for Database {
    fn fmt(&self, f: &mut fmt::Formatter) -> fmt::Result {
        write!(f, "{}:{}/{}", self.host, self.port, self.name)
    }
}
