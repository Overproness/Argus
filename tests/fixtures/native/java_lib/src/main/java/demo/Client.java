package demo;

import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URI;

public class Client {
    /** No connect or read timeout: HttpURLConnection waits forever by default. */
    public static String fetch(String url) throws Exception {
        HttpURLConnection c = (HttpURLConnection) URI.create(url).toURL().openConnection();
        try (InputStream in = c.getInputStream()) {
            return new String(in.readAllBytes());
        }
    }
}
