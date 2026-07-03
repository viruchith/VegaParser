package main

import "fmt"

// Greeter says hello.
func Hello(name string) string {
	return fmt.Sprintf("hello %s", name)
}

type User struct {
	Name string
}
