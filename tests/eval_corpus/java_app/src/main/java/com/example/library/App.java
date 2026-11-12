package com.example.library;

import com.example.library.api.LibraryController;
import com.example.library.repo.BookRepository;
import com.example.library.service.BookService;
import com.example.library.service.CatalogClient;

public class App {
    public static void main(String[] args) {
        BookRepository repository = new BookRepository();
        BookService service = new BookService(repository);
        CatalogClient catalog = new CatalogClient();
        LibraryController controller = new LibraryController(service);
        controller.listBooks();
        System.out.println(catalog.fetchTitle("9780306406157"));
    }
}
