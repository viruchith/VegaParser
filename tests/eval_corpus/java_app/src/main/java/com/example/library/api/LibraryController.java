package com.example.library.api;

import com.example.library.model.Book;
import com.example.library.service.BookService;
import java.util.List;
import org.springframework.web.bind.annotation.GetMapping;

public class LibraryController {
    private final BookService service;

    public LibraryController(BookService service) {
        this.service = service;
    }

    @GetMapping("/books/{isbn}")
    public Book getBook(String isbn) {
        return service.findByIsbn(isbn);
    }

    public List<Book> listBooks() {
        return List.of();
    }
}
