package com.example.library.repo;

import com.example.library.model.Book;
import java.util.HashMap;
import java.util.Map;

public class BookRepository {
    private final Map<String, Book> books = new HashMap<>();

    public Book findByIsbn(String isbn) {
        return books.get(isbn);
    }

    public void save(Book book) {
        books.put(book.getIsbn(), book);
    }
}
