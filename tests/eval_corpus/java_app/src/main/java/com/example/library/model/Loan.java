package com.example.library.model;

public class Loan {
    private final String isbn;
    private final String borrower;
    private final String dueDate;

    public Loan(String isbn, String borrower, String dueDate) {
        this.isbn = isbn;
        this.borrower = borrower;
        this.dueDate = dueDate;
    }
}
