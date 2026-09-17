<?php
namespace App;

use GuzzleHttp\Client;

class Billing
{
    private Client $client;

    public function chargeAll(array $ids)
    {
        foreach ($ids as $id) {
            $user = User::find($id);
            $this->charge($user);
        }
    }

    public function charge($user)
    {
        return $this->client->post('https://pay/charge', ['json' => $user]);
    }

    public function chargeBounded($user)
    {
        return $this->client->post('https://pay/charge', ['json' => $user, 'timeout' => 5]);
    }
}
