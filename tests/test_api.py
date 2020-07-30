from gevent import monkey
monkey.patch_all()


import random
from uuid import uuid4
from time import sleep
from multiprocessing import Process
import unittest
import requests
import rom.util
from flask_socketio import SocketIO
from hydraChess.config import TestingConfig
from hydraChess.__main__ import app
from hydraChess.models import User, Game


class TestGamesApi(unittest.TestCase):
    process = None
    @classmethod
    def setUpClass(cls):
        app.config.from_object(TestingConfig)
        rom.util.set_connection_settings(db=app.config['REDIS_DB_ID'])
        rom.util.use_null_session()

        sio = SocketIO(app, message_queue=app.config['SOCKET_IO_URL'])

        cls.process = Process(
            target=sio.run,
            args=(app,),
            kwargs={
                'port': app.config['PORT'],
                'debug': True,
                'use_reloader': False
            }
        )
        cls.process.start()
        sleep(3)

    def setUp(self):
        self.user_data = {
            'login': uuid4().hex[:15],
            'password': 'testtesttest',
            'confirm_password': 'testtesttest',
            'submit': 'Register'
        }

        resp = requests.post(
            app.config['HOST'] + 'register',
            data=self.user_data,
        )

        self.assertIn('lobby', resp.url)

    def test_games_played_bad_login(self):
        nonexistent_login = uuid4().hex
        url = app.config['HOST'] + 'api/v1.x/games_played'
        resp = requests.get(url, data={'nickname': nonexistent_login})
        self.assertEqual(resp.json(), {'message': "User doesn't exist"})
        self.assertEqual(resp.status_code, 400)

    def test_games_played_value(self):
        for i in range(100):
            user = User.get_by(login=self.user_data['login'])
            user.games_played = i
            user.save()

            url = app.config['HOST'] + 'api/v1.x/games_played'
            resp = requests.get(url, data={'nickname': user.login})
            self.assertEqual(resp.json(), {'games_played': i})
            self.assertEqual(resp.status_code, 200)

    def test_games_list_bad_login(self):
        nonexistent_login = uuid4().hex
        url = app.config['HOST'] + 'api/v1.x/games_list'
        resp = requests.get(url, data={'nickname': nonexistent_login})
        self.assertEqual(resp.json(), {'message': "User doesn't exist"})
        self.assertEqual(resp.status_code, 400)

    def test_games_list_bad_start_from_type(self):
        url = app.config['HOST'] + 'api/v1.x/games_list'

        for val in ("str", 3.4):
            data = {
                'nickname': self.user_data['login'],
                'start_from': val
            }
            resp = requests.get(url, data=data)
            self.assertIn("message", resp.json())
            self.assertIn("start_from", resp.json()["message"])
            self.assertEqual(resp.status_code, 400)

    def test_games_list_bad_size_type(self):
        url = app.config['HOST'] + 'api/v1.x/games_list'

        for val in ("str", 3.4):
            data = {
                'nickname': self.user_data['login'],
                'size': val
            }
            resp = requests.get(url, data=data)
            self.assertIn("message", resp.json())
            self.assertIn("size", resp.json()["message"])
            self.assertEqual(resp.status_code, 400)

    def test_games_list_bad_size_value(self):
        url = app.config['HOST'] + 'api/v1.x/games_list'

        for val in (6, 15, -3, 18, 10 ** 8, 101):
            data = {
                'nickname': self.user_data['login'],
                'size': val
            }
            resp = requests.get(url, data=data)
            self.assertEqual(
                resp.json(),
                {"message": {"size": f"{val} is not a valid choice"}}
            )
            self.assertEqual(resp.status_code, 400)

    def test_games_list_start_from_more_than_games_played(self):
        game = Game()
        white_user = User.get_by(login=self.user_data['login'])
        black_user = User(login=uuid4().hex[:15])
        black_user.save()
        game.black_user = black_user
        game.white_user = white_user
        game.result = "1-0"
        game.save()

        for _ in range(15):
            cur_game = game.copy()
            cur_game.save()
            white_user.append_game_id(cur_game.id)
            white_user.games_played += 1
            white_user.save()

        url = app.config['HOST'] + 'api/v1.x/games_list'

        for start_from_val in range(15, 30):
            data = {
                'nickname': self.user_data['login'],
                'start_from': start_from_val
            }
            resp = requests.get(url, data=data)
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json(), {"games": []})

    def test_games_list_all_size_values(self):
        game = Game()
        white_user = User.get_by(login=self.user_data['login'])
        black_user = User(login=uuid4().hex[:15])
        black_user.save()
        game.black_user = black_user
        game.white_user = white_user
        game.result = "1-0"
        game.save()

        for _ in range(300):
            cur_game = game.copy()
            cur_game.save()
            white_user.append_game_id(cur_game.id)
            white_user.games_played += 1
            white_user.save()

        url = app.config['HOST'] + 'api/v1.x/games_list'
        for size_val in (10, 20, 50, 100):
            data = {
                'nickname': self.user_data['login'],
                'size': size_val
            }
            resp = requests.get(url, data=data)
            self.assertEqual(resp.status_code, 200)
            self.assertIn("games", resp.json())
            self.assertIsInstance(resp.json()["games"], list)
            self.assertEqual(len(resp.json()["games"]), size_val)

    def test_response_on_valid_request(self):
        game = Game()
        white_user = User.get_by(login=self.user_data['login'])
        black_user = User(login=uuid4().hex[:15])
        black_user.save()
        game.black_user = black_user
        game.white_user = white_user
        game.result = random.choice(["1-0", "0-1", "1/2-1/2"])
        game.save()

        for i in range(150):
            cur_game = game.copy()
            cur_game.save()
            white_user.append_game_id(cur_game.id)
            white_user.games_played += 1
            white_user.save()

        url = app.config['HOST'] + 'api/v1.x/games_list'

        user = User.get_by(login=self.user_data['login'])
        games_played = user.games_played
        game_ids = user.game_ids

        for size_val in (10, 20, 50, 100):
            data = {
                'nickname': self.user_data['login'],
                'size': size_val
            }
            for start_from_val in range(0, 150, 5):
                data['start_from'] = start_from_val
                resp = requests.get(url, data=data)

                range_end = min(games_played, start_from_val + data['size'])

                expected_size = range_end - start_from_val

                json = resp.json()

                self.assertEqual(resp.status_code, 200)
                self.assertIn("games", json)
                self.assertIsInstance(json["games"], list)
                self.assertEqual(len(json["games"]), expected_size)

                for i in range(start_from_val, range_end):
                    game = Game.get(game_ids[i])
                    expected_data = {
                        'white_player': game.white_user.login,
                        'black_player': game.black_user.login,
                        'result': game.result,
                        'id': game.id
                    }
                    response_data = json["games"][i - start_from_val]

                    self.assertEqual(expected_data, response_data)

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()


if __name__ == "__main__":
    unittest.main()