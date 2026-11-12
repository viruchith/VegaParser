package com.example.library.util;

public final class Isbn {
    private Isbn() {
    }

    public static void validate(String isbn) {
        if (isbn == null || isbn.length() != 13) {
            throw new IllegalArgumentException("ISBN must have 13 digits");
        }
    }
}
