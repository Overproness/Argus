import unittest
from decimal import Decimal
from relay.catalog import LocalClient, names_for, delivery_windows
from relay.money import invoice_total
from relay.models import Job
from relay.retention import drop_expired


class ServiceTests(unittest.TestCase):
    def test_local_catalog(self):
        self.assertEqual(names_for(LocalClient({"parcel": "Parcel"}), ["parcel"]), ["Parcel"])
        self.assertEqual(len(delivery_windows()), 21)

    def test_invoice(self):
        self.assertEqual(invoice_total([("0.10", 3)]), Decimal("0.30"))

    def test_active_job_is_retained(self):
        jobs = [Job("pending", None)]
        self.assertEqual(drop_expired(jobs, 120), jobs)
