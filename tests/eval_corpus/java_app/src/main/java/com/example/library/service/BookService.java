package com.example.library.service;

import com.example.library.model.Book;
import com.example.library.repo.BookRepository;
import com.example.library.util.Isbn;

public class BookService {
    private final BookRepository repository;

    public BookService(BookRepository repository) {
        this.repository = repository;
    }

    public Book findByIsbn(String isbn) {
        Isbn.validate(isbn);
        return repository.findByIsbn(isbn);
    }

    public boolean checkout(String isbn, String borrower) {
        Book book = findByIsbn(isbn);
        if (book == null || book.isCheckedOut()) {
            return false;
        }
        book.checkOut(borrower);
        repository.save(book);
        return true;
    }
}
