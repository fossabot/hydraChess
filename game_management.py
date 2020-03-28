from typing import Dict
import chess
from flask_socketio import close_room
from flask_celery import make_celery
from main import app, sio
from models import db, Game, User


celery = make_celery(app)


@celery.task(name='start_game', ignore_result=True)
def start_game(game_id: int) -> None:
    '''Marks game as started.
       Emits fen, pieces color, info about the opponent
        and rating changes to players'''

    game = db.session.query(Game).get(game_id)
    game.is_started = 1
    game.fen = chess.STARTING_FEN
    db.session.merge(game)
    db.session.commit()

    rating_changes = get_rating_changes(game_id)

    sio.emit('game_started',
             {"fen": game.fen,
              "color": "white",
              "opp_nickname": game.user_black_pieces.login,
              "opp_rating": int(game.user_black_pieces.rating),
              "rating_changes": rating_changes["white"].to_dict()},
             room=game.user_white_pieces.sid)
    sio.emit('game_started',
             {"fen": game.fen,
              "color": "black",
              "opp_nickname": game.user_white_pieces.login,
              "opp_rating": int(game.user_white_pieces.rating),
              "rating_changes": rating_changes["black"].to_dict()},
             room=game.user_black_pieces.sid)


@celery.task(name='update_game', ignore_result=True)
def update_game(game_id: int, user_id: int, move_san: str = "",
                draw_offer: bool = False, surrender: bool = False) -> None:
    '''Updates game state by user's data.
       Calls end_game(...) if the game is ended.'''
    # TODO: draw offer and surrender support

    game = db.session.query(Game).get(game_id)

    if game.is_finished:
        return

    board = chess.Board(game.fen)
    user_white = game.user_white_pieces_id == user_id

    if (user_white and board.turn == chess.BLACK) or\
       (not user_white and board.turn == chess.WHITE):
        print("Wrong move side.")
        return

    if move_san:
        try:
            move = board.push_san(move_san)
            game.fen = board.fen()
            db.session.merge(game)
            db.session.commit()

            result = board.result()
            sio.emit('game_updated',
                     {"san": move_san},
                     room=game_id)
            if result != '*':
                end_game.delay(game_id, result)
        except ValueError as ex:
            pass


@celery.task(name="reconnect", ignore_result=True)
def reconnect(game_id: int, user_id: int) -> None:
    '''Emits fen, pieces color, info about the opponent and
        rating changes to player'''

    game = db.session.query(Game).get(game_id)

    rating_changes = get_rating_changes(game_id)

    if user_id == game.user_white_pieces_id:
        sid = game.user_white_pieces.sid
        sio.emit('game_started',
                 {'fen': game.fen,
                  "color": "white",
                  "opp_nickname": game.user_black_pieces.login,
                  "opp_rating": int(game.user_black_pieces.rating),
                  "rating_changes": rating_changes["white"].to_dict()},
                 room=sid)
    else:
        sid = game.user_black_pieces.sid
        sio.emit('game_started',
                 {'fen': game.fen,
                  "color": "black",
                  "opp_nickname": game.user_white_pieces.login,
                  "opp_rating": int(game.user_white_pieces.rating),
                  "rating_changes": rating_changes["black"].to_dict()},
                 room=sid)


@celery.task(name='end_game', ignore_result=True)
def end_game(game_id: str, result: str,
             reason: str = '', update_stats=True) -> None:
    '''Marks game as finished, emits 'game_ended' signal to users,
     closes the room,
     recalculates ratings and k-factors if update_stats is True'''

    game = db.session.query(Game).get(game_id)

    results = {'1-0': ('won', 'lost'),
               '1/2-1/2': ('draw', 'draw'),
               '0-1': ('lost', 'won'),
               '-': ('interrupted', 'interrupted')}

    result_white, result_black = results[result]
    sio.emit('game_ended',
             {'result': result_white,
              'reason': reason},
             room=game.user_white_pieces.sid)
    sio.emit('game_ended',
             {'result': result_black,
              'reason': reason},
             room=game.user_black_pieces.sid)

    close_room(game_id, namespace='/')

    game.is_finished = 1
    game.result = result

    game.user_white_pieces.cur_game_id = None
    game.user_black_pieces.cur_game_id = None

    db.session.merge(game)
    db.session.commit()

    if update_stats is False:
        return

    rating_changes = get_rating_changes(game_id)

    game.user_white_pieces.games_played += 1
    game.user_black_pieces.games_played += 1

    db.session.merge(game.user_white_pieces)
    db.session.merge(game.user_black_pieces)
    db.session.commit()

    if result == "1-0":
        update_rating.delay(game.user_white_pieces_id,
                            rating_changes["white"].win)
        update_rating.delay(game.user_black_pieces_id,
                            rating_changes["black"].lose)
    elif result == "1/2-1/2":
        update_rating.delay(game.user_white_pieces_id,
                            rating_changes["white"].draw)
        update_rating.delay(game.user_black_pieces_id,
                            rating_changes["black"].draw)
    elif result == "0-1":
        update_rating.delay(game.user_white_pieces_id,
                            rating_changes["white"].lose)
        update_rating.delay(game.user_black_pieces_id,
                            rating_changes["black"].win)

    update_k_factor.apply_async(args=(game.user_white_pieces_id,), countdown=3)
    update_k_factor.apply_async(args=(game.user_black_pieces_id,), countdown=3)


class RatingChange:
    '''Class for comfortable work with rating changes'''
    def __init__(self, win=None, draw=None, lose=None):
        self.win = win
        self.draw = draw
        self.lose = lose

    @staticmethod
    def from_formula(k: int, e: float):
        '''Build up RatingChange object from ELO rating system formula'''
        win = k * (1 - e)
        draw = k * (0.5 - e)
        lose = k * (-e)
        return RatingChange(win, draw, lose)

    def to_dict(self, to_int=True):
        '''Get rating changes in dict'''
        if to_int:
            return {"win": int(self.win),
                    "draw": int(self.draw),
                    "lose": int(self.lose)}
        return {"win": self.win,
                "draw": self.draw,
                "lose": self.lose}


def get_rating_changes(game_id: int) -> Dict[str, RatingChange]:
    '''Returns rating changes for game in dict.
       Example: {'white': RatingChange,
                 'black': RatingChange}'''
    game = db.session.query(Game).get(game_id)
    r_white = game.user_white_pieces.rating
    r_black = game.user_black_pieces.rating

    R_white = 10 ** (r_white / 400)
    R_black = 10 ** (r_black / 400)

    R_sum = R_white + R_black

    E_white = R_white / R_sum
    E_black = R_black / R_sum

    k_factor_white = game.user_white_pieces.k_factor
    k_factor_black = game.user_black_pieces.k_factor

    rating_change_white = RatingChange.from_formula(k_factor_white, E_white)
    rating_change_black = RatingChange.from_formula(k_factor_black, E_black)

    return {"white": rating_change_white,
            "black": rating_change_black}


@celery.task(name="update_k_factor", ignore_result=True)
def update_k_factor(user_id):
    '''Updates k_factor by FIDE rules (after 2014)'''
    user = db.session.query(User).get(user_id)

    if user.k_factor == 40 and user.games_played >= 30:
        user.k_factor = 20

    if user.k_factor == 20 and user.games_played >= 30 and user.rating >= 2400:
        user.k_factor = 10

    db.session.merge(user)
    db.session.commit()


@celery.task(name="update_rating")
def update_rating(user_id, rating_delta):
    '''Update database info about user's rating'''
    user = db.session.query(User).get(user_id)
    user.rating += rating_delta
    db.session.merge(user)
    db.session.commit()


@celery.task(name="send_message")
def send_message(game_id, sender, message):
    '''Send chat message to game players'''
    sio.emit('get_message',
             {'sender': sender,
              'message': message},
             room=game_id)
