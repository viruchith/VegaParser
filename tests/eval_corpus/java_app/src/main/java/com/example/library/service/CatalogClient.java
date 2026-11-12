package com.example.library.service;

public class CatalogClient {
    private static final String CATALOG_URL = "https://catalog.example.com/v2/books";

    public String fetchTitle(String isbn) {
        return CATALOG_URL + "/" + isbn;
    }
}
