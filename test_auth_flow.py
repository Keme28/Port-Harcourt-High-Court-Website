import os
import sys
import unittest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "app"))

from app import app
import db


class AuthFlowTests(unittest.TestCase):
    def test_login_page_exists(self):
        client = TestClient(app)
        response = client.get('/login')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Sign in', response.text)

    def test_setup_page_exists_or_redirects_if_configured(self):
        client = TestClient(app, follow_redirects=False)
        response = client.get('/setup')
        if db.user_count() == 0:
            self.assertEqual(response.status_code, 200)
            self.assertIn('Create your account', response.text)
        else:
            self.assertEqual(response.status_code, 303)
            self.assertEqual(response.headers['location'], '/login')


if __name__ == '__main__':
    unittest.main()

