package com.example.library.model;

public class Book {
    private final String isbn;
    private final String title;
    private String borrower;

    public Book(String isbn, String title) {
        this.isbn = isbn;
        this.title = title;
    }

    public String getIsbn() {
        return isbn;
    }

    public String getTitle() {
        return title;
    }

    public boolean isCheckedOut() {
        return borrower != null;
    }

    public void checkOut(String borrower) {
        this.borrower = borrower;
    }
}
