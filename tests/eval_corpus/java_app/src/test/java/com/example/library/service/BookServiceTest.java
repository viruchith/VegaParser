package com.example.library.service;

import com.example.library.model.Book;
import com.example.library.repo.BookRepository;
import org.junit.jupiter.api.Test;

class BookServiceTest {
    @Test
    void checkoutMarksBookAsBorrowed() {
        BookRepository repository = new BookRepository();
        repository.save(new Book("9780306406157", "Title"));
        BookService service = new BookService(repository);
        service.checkout("9780306406157", "ada");
    }
}
