<?php
namespace App;

use GuzzleHttp\Client;
use GuzzleHttp\Exception\RequestException;

class Quotes
{
    private Client $client;

    public function fetch(string $url)
    {
        for ($i = 0; $i < 3; $i++) {
            try {
                return $this->client->get($url);
            } catch (RequestException $e) {
                continue;
            }
        }
        return null;
    }
}
