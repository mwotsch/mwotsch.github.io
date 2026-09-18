#!/usr/bin/env python3
"""
Chess Rating System - Generates HTML rating tables from game data
Implements ELO rating system with interactive HTML output
"""

import math
import json
import os
from collections import Counter


class ChessRatingSystem:
    def __init__(self, k_factor=None, initial_rating=1200):
        self.k_factor = k_factor  # None follows FIDE's schedule; a number overrides it
        self.initial_rating = initial_rating
        self.players = {}
        self.games = []
        self.byes = []  # Full-point byes: {'player', 'date_raw', 'sequence'}
        self.tau = 0.5  # Glicko-2 system constant, constrains volatility change
        self.glicko_convergence = 0.000001  # Convergence tolerance for the volatility solver
        self.uscf_event = []  # Games buffered for the current USCF event
        self.uscf_event_date = None
    
    def init_player(self, name):
        """Initialize a new player with default stats"""
        if name not in self.players:
            self.players[name] = {
                'name': name,
                'rating': self.initial_rating,  # Published ELO, the exact rating rounded
                'elo_exact': float(self.initial_rating),  # FIDE keeps ratings as floats
                'elo_k10': False,  # Set once a published rating reaches 2400 (FIDE 8.3.3)
                'glicko_rating': 1200,  # Glicko-2 starting at 1200
                'glicko_deviation': 350,  # Initial rating deviation
                'glicko_volatility': 0.06,  # Initial volatility
                'uscf_rating': self.initial_rating,  # USCF rating, rounded for display
                'uscf_exact': float(self.initial_rating),  # USCF keeps ratings as floats
                'uscf_games': 0,  # N: rated games before the current event
                'uscf_wins': 0,  # Rated games won, for the personal absolute floor
                'uscf_draws': 0,  # Rated games drawn, for the personal absolute floor
                'uscf_losses': 0,  # Rated games lost, for the all-losses special case
                'uscf_events': 0,  # Events of three or more rated games
                'uscf_peak': None,  # Highest established rating, for the rating floor
                'games': 0,
                'wins': 0,
                'draws': 0,
                'losses': 0,
                'opponents': {},
                'biggest_wins': [],  # Track biggest wins by rating difference
                'biggest_upsets': [],  # Track biggest losses (upsets) by rating difference
                # Track highest and lowest ratings for each system
                'highest_elo': self.initial_rating,
                'lowest_elo': self.initial_rating,
                'highest_glicko': 1200,
                'lowest_glicko': 1200,
                'highest_uscf': self.initial_rating,
                'lowest_uscf': self.initial_rating
            }
    
    def get_elo_k_factor(self, player):
        """FIDE development coefficient (Rating Regulations 8.3.3).

        FIDE also gives K = 40 to players under 18 rated below 2300; games.txt
        carries no birth dates, so that case cannot be applied here.
        """
        if self.k_factor is not None:
            return self.k_factor
        if player['elo_k10']:
            return 10
        if player['games'] < 30:
            return 40
        return 20

    def calculate_elo_change(self, rating_a, rating_b, score_a, k_factor):
        """Calculate ELO rating change for player A"""
        difference = rating_b - rating_a
        if rating_a < 2650:
            # FIDE 8.3.1: a gap of more than 400 points counts as 400
            difference = max(-400.0, min(400.0, difference))
        expected_a = 1 / (1 + math.pow(10, difference / 400))
        return k_factor * (score_a - expected_a)

    def publish_elo_rating(self, player):
        """Round the exact rating for publication and latch the 2400 threshold."""
        player['rating'] = round(player['elo_exact'])
        if player['rating'] >= 2400:
            player['elo_k10'] = True
    
    def glicko2_scale(self, rating):
        """Convert rating to Glicko-2 scale"""
        return (rating - 1200) / 173.7178
    
    def glicko2_unscale(self, mu):
        """Convert back from Glicko-2 scale"""
        return mu * 173.7178 + 1200
    
    def glicko2_g(self, phi):
        """G function for Glicko-2"""
        return 1 / math.sqrt(1 + 3 * phi * phi / (math.pi * math.pi))
    
    def glicko2_e(self, mu, mu_j, phi_j):
        """Expected score function for Glicko-2"""
        expected = 1 / (1 + math.exp(-self.glicko2_g(phi_j) * (mu - mu_j)))
        # At extreme rating gaps this reaches exactly 0.0 or 1.0 in floating point,
        # and the variance 1 / (g^2 * E * (1 - E)) would then divide by zero.
        return min(max(expected, 1e-12), 1 - 1e-12)

    def glicko2_volatility(self, phi, v, delta, sigma):
        """Solve for the new volatility sigma' (Glicko-2 step 5).

        Finds the root of Glickman's f(x) with the Illinois algorithm, exactly as
        described in "Example of the Glicko-2 system".
        """
        alpha = math.log(sigma * sigma)

        def f(x):
            exp_x = math.exp(x)
            numerator = exp_x * (delta * delta - phi * phi - v - exp_x)
            denominator = 2 * (phi * phi + v + exp_x) ** 2
            return numerator / denominator - (x - alpha) / (self.tau * self.tau)

        # Bracket the root
        x_a = alpha
        if delta * delta > phi * phi + v:
            x_b = math.log(delta * delta - phi * phi - v)
        else:
            k = 1
            while f(alpha - k * self.tau) < 0:
                k += 1
            x_b = alpha - k * self.tau

        f_a, f_b = f(x_a), f(x_b)
        while abs(x_b - x_a) > self.glicko_convergence:
            x_c = x_a + (x_a - x_b) * f_a / (f_b - f_a)
            f_c = f(x_c)
            if f_c * f_b <= 0:
                x_a, f_a = x_b, f_b
            else:
                f_a = f_a / 2
            x_b, f_b = x_c, f_c

        return math.exp(x_a / 2)
    
    # --- USCF rating system ---------------------------------------------
    # Glickman & Doan, "The US Chess Rating system" (rating.system.pdf).
    # USCF rates a whole event at once, so games are buffered and the update runs
    # when the event closes rather than after each game.
    USCF_ABSOLUTE_FLOOR = 100        # Section 5
    USCF_BONUS_MULTIPLIER = 10       # B, effective January 1, 2025 (Section 4.2)
    USCF_SPECIAL_TOLERANCE = 1e-7    # epsilon in Section 4.1
    USCF_SPECIAL_CAP = 2700          # Section 4.1
    USCF_ESTABLISHED_GAMES = 25      # A rating is "established" above this (Section 1)

    def uscf_effective_games(self, player, games=None):
        """Section 3: the effective number of games N', never more than 50."""
        rating = player['uscf_exact']
        if rating <= 2355:
            n_star = 50 / math.sqrt(0.662 + 0.00000739 * (2569 - rating) ** 2)
        else:
            n_star = 50.0
        return min(player['uscf_games'] if games is None else games, n_star)

    def uscf_pwe(self, rating, opponent_rating):
        """Section 4.1: the provisional winning expectancy."""
        if rating <= opponent_rating - 400:
            return 0.0
        if rating >= opponent_rating + 400:
            return 1.0
        return 0.5 + (rating - opponent_rating) / 800

    def uscf_uses_special(self, player):
        """Section 4: eight or fewer games, or a career of all wins or all losses."""
        played = player['uscf_games']
        if played <= 8:
            return True
        return player['uscf_wins'] == played or player['uscf_losses'] == played

    def uscf_special_rating(self, player, opponents, score, games=None):
        """Section 4.1: the rating at which expected score equals attained score.

        The objective is piecewise linear in the rating, so it is solved by walking
        between its knots rather than by a fixed-step iteration.
        """
        prior = player['uscf_exact']
        played = player['uscf_games']
        effective = self.uscf_effective_games(player, games)
        epsilon = self.USCF_SPECIAL_TOLERANCE
        
        if played > 0 and player['uscf_wins'] == played:
            prior_adj, score_adj = prior - 400, score + effective
        elif played > 0 and player['uscf_losses'] == played:
            prior_adj, score_adj = prior + 400, score
        else:
            prior_adj, score_adj = prior, score + effective / 2
        
        def objective(rating):
            total = effective * self.uscf_pwe(rating, prior_adj)
            total += sum(self.uscf_pwe(rating, opponent) for opponent in opponents)
            return total - score_adj
        
        anchors = [prior_adj] + list(opponents)
        knots = sorted({r - 400 for r in anchors} | {r + 400 for r in anchors})
        
        estimate = prior_adj
        for _ in range(1000):  # guard; the walk below always moves towards the root
            value = objective(estimate)
            previous = estimate
            if value > epsilon:
                below = [knot for knot in knots if knot < estimate]
                if not below:
                    break
                z_a = max(below)
                f_a = objective(z_a)
                if abs(value - f_a) < epsilon:
                    estimate = z_a
                else:
                    step = estimate - value * (estimate - z_a) / (value - f_a)
                    estimate = z_a if step < z_a else step
            elif value < -epsilon:
                above = [knot for knot in knots if knot > estimate]
                if not above:
                    break
                z_b = min(above)
                f_b = objective(z_b)
                if abs(f_b - value) < epsilon:
                    estimate = z_b
                else:
                    step = estimate - value * (z_b - estimate) / (f_b - value)
                    estimate = z_b if step > z_b else step
            else:
                # Step 4: on a flat stretch of the objective every rating in the
                # stretch fits the score, so take the one closest to the prior.
                nearby = sum(1 for opponent in opponents if abs(estimate - opponent) < 400)
                if abs(estimate - prior_adj) < 400:
                    nearby += 1
                if nearby == 0:
                    below = [knot for knot in knots if knot <= estimate]
                    above = [knot for knot in knots if knot >= estimate]
                    z_a = max(below) if below else estimate
                    z_b = min(above) if above else estimate
                    estimate = min(max(prior, z_a), z_b)
                break
            if estimate == previous:
                break
        
        return min(estimate, self.USCF_SPECIAL_CAP)

    def uscf_standard_rating(self, player, opponents, score, times_faced):
        """Section 4.2: K = 800 / (N' + m), with the bonus term where it applies."""
        prior = player['uscf_exact']
        played = len(opponents)
        k_factor = 800 / (self.uscf_effective_games(player) + played)
        expected = sum(1 / (1 + math.pow(10, (opponent - prior) / 400)) for opponent in opponents)
        change = k_factor * (score - expected)
        
        most_faced = max(times_faced) if times_faced else 0
        earns_bonus = (played > 3 and most_faced <= 2) or (played == 3 and most_faced <= 1)
        if earns_bonus:
            rounds = max(played, 4)  # three-round events count as four here
            change += max(0.0, change - self.USCF_BONUS_MULTIPLIER * math.sqrt(rounds))
        return prior + change

    def uscf_floor(self, player):
        """Section 5: the personal absolute floor, raised by the player's peak."""
        floor = min(100 + 4 * player['uscf_wins'] + 2 * player['uscf_draws']
                    + player['uscf_events'], 150)
        peak = player['uscf_peak']
        if peak is not None:
            candidate = round(peak) - 200
            if candidate >= 1200:  # the higher floors run 1200, 1300, ... 2100
                floor = max(floor, min(2100, int(candidate // 100) * 100))
        return max(self.USCF_ABSOLUTE_FLOOR, floor)

    def queue_uscf_game(self, white_player, black_player, white_score, date_raw):
        """Buffer a game for its USCF event, closing the previous event first."""
        if self.uscf_event and (date_raw is None or date_raw != self.uscf_event_date):
            self.flush_uscf_event()
        self.uscf_event_date = date_raw
        self.uscf_event.append((white_player, black_player, white_score))
        if date_raw is None:  # an undated game is an event of its own
            self.flush_uscf_event()

    def flush_uscf_event(self):
        """Run the five-step event algorithm over the games buffered so far."""
        event, self.uscf_event = self.uscf_event, []
        self.uscf_event_date = None
        if not event:
            return
        
        results = {}
        for white_player, black_player, white_score in event:
            results.setdefault(white_player, []).append((black_player, white_score))
            results.setdefault(black_player, []).append((white_player, 1 - white_score))
        
        scores = {name: sum(score for _, score in games) for name, games in results.items()}
        
        # Step 3: a first estimate for players who have never been rated. It is only
        # used as a measure of their strength while rating everyone else.
        pre_event = {name: self.players[name]['uscf_exact'] for name in results}
        ratings = dict(pre_event)
        for name, games in results.items():
            if self.players[name]['uscf_games'] == 0:
                opponents = [pre_event[opponent] for opponent, _ in games]
                estimate = self.uscf_special_rating(self.players[name], opponents, scores[name], games=1)
                ratings[name] = max(float(self.USCF_ABSOLUTE_FLOOR), estimate)
        
        # Steps 4 and 5: rate everyone against the opponents' pre-event ratings, then
        # rate everyone again against the intermediate ratings step 4 produced. Each
        # player's own rating going into both steps is their pre-event rating.
        for _ in range(2):
            updated = {}
            for name, games in results.items():
                player = self.players[name]
                opponents = [ratings[opponent] for opponent, _ in games]
                if self.uscf_uses_special(player):
                    new_rating = self.uscf_special_rating(player, opponents, scores[name])
                else:
                    times_faced = Counter(opponent for opponent, _ in games).values()
                    new_rating = self.uscf_standard_rating(player, opponents, scores[name],
                                                           list(times_faced))
                updated[name] = max(float(self.USCF_ABSOLUTE_FLOOR), new_rating)
            ratings = updated
        
        for name, games in results.items():
            player = self.players[name]
            player['uscf_games'] += len(games)
            player['uscf_wins'] += sum(1 for _, score in games if score == 1.0)
            player['uscf_draws'] += sum(1 for _, score in games if score == 0.5)
            player['uscf_losses'] += sum(1 for _, score in games if score == 0.0)
            if len(games) >= 3:
                player['uscf_events'] += 1
            
            player['uscf_exact'] = max(ratings[name], float(self.uscf_floor(player)))
            player['uscf_rating'] = round(player['uscf_exact'])
            if player['uscf_games'] > self.USCF_ESTABLISHED_GAMES:
                peak = player['uscf_peak']
                player['uscf_peak'] = max(peak, player['uscf_exact']) if peak else player['uscf_exact']
            
            player['highest_uscf'] = max(player['highest_uscf'], player['uscf_rating'])
            player['lowest_uscf'] = min(player['lowest_uscf'], player['uscf_rating'])
            # The rating only moves when the event closes, so the player's last game
            # of the event is the one that carries the new rating.
            if player.get('rating_history'):
                player['rating_history'][-1]['uscf'] = player['uscf_rating']

    def update_biggest_wins(self, player, win_record):
        """Update player's biggest wins list"""
        player['biggest_wins'].append(win_record)
        # Sort by rating difference (descending) and keep top 5
        player['biggest_wins'].sort(key=lambda x: x['rating_diff'], reverse=True)
        player['biggest_wins'] = player['biggest_wins'][:5]
    
    def update_biggest_upsets(self, player, upset_record):
        """Update player's biggest upsets (losses) list"""
        player['biggest_upsets'].append(upset_record)
        # Sort by rating difference (descending) and keep top 5
        player['biggest_upsets'].sort(key=lambda x: x['rating_diff'], reverse=True)
        player['biggest_upsets'] = player['biggest_upsets'][:5]
    
    def update_rating_extremes(self, player):
        """Update highest and lowest ratings for all systems"""
        # ELO extremes
        if player['rating'] > player['highest_elo']:
            player['highest_elo'] = player['rating']
        if player['rating'] < player['lowest_elo']:
            player['lowest_elo'] = player['rating']
        
        # Glicko-2 extremes
        if player['glicko_rating'] > player['highest_glicko']:
            player['highest_glicko'] = player['glicko_rating']
        if player['glicko_rating'] < player['lowest_glicko']:
            player['lowest_glicko'] = player['glicko_rating']
    
    def update_glicko2_ratings(self, player_a, player_b, score_a):
        """Update Glicko-2 ratings for both players"""
        # Convert to Glicko-2 scale
        mu_a = self.glicko2_scale(player_a['glicko_rating'])
        mu_b = self.glicko2_scale(player_b['glicko_rating'])
        phi_a = player_a['glicko_deviation'] / 173.7178
        phi_b = player_b['glicko_deviation'] / 173.7178
        sigma_a = player_a['glicko_volatility']
        sigma_b = player_b['glicko_volatility']
        
        # Update player A
        g_b = self.glicko2_g(phi_b)
        e_ab = self.glicko2_e(mu_a, mu_b, phi_b)
        
        v_a = 1 / (g_b * g_b * e_ab * (1 - e_ab))
        delta_a = v_a * g_b * (score_a - e_ab)
        
        # Update volatility
        sigma_a_new = self.glicko2_volatility(phi_a, v_a, delta_a, sigma_a)
        
        # Update deviation and rating
        phi_a_new = math.sqrt(phi_a * phi_a + sigma_a_new * sigma_a_new)
        phi_a_new = 1 / math.sqrt(1 / (phi_a_new * phi_a_new) + 1 / v_a)
        mu_a_new = mu_a + phi_a_new * phi_a_new * g_b * (score_a - e_ab)
        
        # Update player B
        g_a = self.glicko2_g(phi_a)
        e_ba = self.glicko2_e(mu_b, mu_a, phi_a)
        score_b = 1 - score_a
        
        v_b = 1 / (g_a * g_a * e_ba * (1 - e_ba))
        delta_b = v_b * g_a * (score_b - e_ba)
        
        sigma_b_new = self.glicko2_volatility(phi_b, v_b, delta_b, sigma_b)
        
        phi_b_new = math.sqrt(phi_b * phi_b + sigma_b_new * sigma_b_new)
        phi_b_new = 1 / math.sqrt(1 / (phi_b_new * phi_b_new) + 1 / v_b)
        mu_b_new = mu_b + phi_b_new * phi_b_new * g_a * (score_b - e_ba)
        
        # Convert back and update
        player_a['glicko_rating'] = round(self.glicko2_unscale(mu_a_new))
        player_a['glicko_deviation'] = round(phi_a_new * 173.7178, 1)
        player_a['glicko_volatility'] = round(sigma_a_new, 4)
        
        player_b['glicko_rating'] = round(self.glicko2_unscale(mu_b_new))
        player_b['glicko_deviation'] = round(phi_b_new * 173.7178, 1)
        player_b['glicko_volatility'] = round(sigma_b_new, 4)
    
    def parse_game_result(self, result):
        """Parse game result string into scores"""
        if result in ["1:0", "1-0"]:
            return 1.0, 0.0
        elif result in ["0:1", "0-1"]:
            return 0.0, 1.0
        elif result in ["0.5:0.5", "0.5-0.5"]:
            return 0.5, 0.5
        else:
            return None, None
    
    def format_date(self, date_str):
        """Format YYYYMMDD date string to human readable format"""
        if not date_str or len(date_str) != 8:
            return None
        
        try:
            year = date_str[:4]
            month = date_str[4:6]
            day = date_str[6:8]
            
            # Convert to readable format like "Jan 1, 2025"
            months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                     'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
            month_name = months[int(month) - 1]
            
            return f"{month_name} {int(day)}, {year}"
        except (ValueError, IndexError):
            return None
    
    def process_game(self, line, game_number):
        """Process a single game line and update ratings"""
        line = line.strip()
        if not line:
            return None
        
        # Parse the game line: "White Player - Black Player Result [Date]"
        parts = line.split()
        if len(parts) < 3:
            return None
        
        # Check if last part is a date (8 digits)
        date_str = None
        if len(parts) > 3 and len(parts[-1]) == 8 and parts[-1].isdigit():
            date_str = parts[-1]
            line_without_date = ' '.join(parts[:-1])
        else:
            line_without_date = line
        
        # Parse player names and result
        line_parts = line_without_date.rsplit(' ', 1)
        if len(line_parts) != 2:
            return None
        
        player_part, result = line_parts
        if ' - ' not in player_part:
            return None
        
        white_player, black_player = player_part.split(' - ', 1)
        white_player = white_player.strip()
        black_player = black_player.strip()
        
        # A bye ("Player - BYE 1-0 DATE") counts as a tournament win but is not a rated game
        if black_player.upper() == 'BYE' or white_player.upper() == 'BYE':
            player = white_player if black_player.upper() == 'BYE' else black_player
            self.byes.append({'player': player, 'date_raw': date_str, 'sequence': game_number})
            return None
        
        # Initialize players if needed
        self.init_player(white_player)
        self.init_player(black_player)
        
        # Parse result
        white_score, black_score = self.parse_game_result(result)
        if white_score is None:
            return None
        
        # Store ratings before the game
        white_rating_before = self.players[white_player]['rating']
        black_rating_before = self.players[black_player]['rating']
        
        # Calculate rating changes from the exact (unrounded) ratings
        white_exact = self.players[white_player]['elo_exact']
        black_exact = self.players[black_player]['elo_exact']
        white_k = self.get_elo_k_factor(self.players[white_player])
        black_k = self.get_elo_k_factor(self.players[black_player])
        
        self.players[white_player]['elo_exact'] += self.calculate_elo_change(
            white_exact, black_exact, white_score, white_k)
        self.players[black_player]['elo_exact'] += self.calculate_elo_change(
            black_exact, white_exact, black_score, black_k)
        
        self.publish_elo_rating(self.players[white_player])
        self.publish_elo_rating(self.players[black_player])
        white_change = self.players[white_player]['rating'] - white_rating_before
        black_change = self.players[black_player]['rating'] - black_rating_before
        
        # Update Glicko-2 ratings
        self.update_glicko2_ratings(self.players[white_player], self.players[black_player], white_score)
        
        # Buffer the game for the USCF event update
        self.queue_uscf_game(white_player, black_player, white_score, date_str)
        
        # Update rating extremes for both players
        self.update_rating_extremes(self.players[white_player])
        self.update_rating_extremes(self.players[black_player])
        
        # Track biggest wins and upsets based on ELO rating difference
        rating_diff = abs(white_rating_before - black_rating_before)
        
        if white_score == 1.0:  # White won
            # Big win for white player (if black was higher rated)
            if black_rating_before > white_rating_before:
                win_record = {
                    'opponent': black_player,
                    'rating_diff': rating_diff,
                    'own_rating': white_rating_before,
                    'opponent_rating': black_rating_before,
                    'game_number': game_number,
                    'date': self.format_date(date_str) if date_str else None,
                    'result': 'Win'
                }
                self.update_biggest_wins(self.players[white_player], win_record)
            
            # Upset for black player (if higher rated)
            if black_rating_before > white_rating_before:
                upset_record = {
                    'opponent': white_player,
                    'rating_diff': rating_diff,
                    'own_rating': black_rating_before,
                    'opponent_rating': white_rating_before,
                    'game_number': game_number,
                    'date': self.format_date(date_str) if date_str else None,
                    'result': 'Loss'
                }
                self.update_biggest_upsets(self.players[black_player], upset_record)
        elif black_score == 1.0:  # Black won
            # Big win for black player (if white was higher rated)
            if white_rating_before > black_rating_before:
                win_record = {
                    'opponent': white_player,
                    'rating_diff': rating_diff,
                    'own_rating': black_rating_before,
                    'opponent_rating': white_rating_before,
                    'game_number': game_number,
                    'date': self.format_date(date_str) if date_str else None,
                    'result': 'Win'
                }
                self.update_biggest_wins(self.players[black_player], win_record)
            
            # Upset for white player (if higher rated)
            if white_rating_before > black_rating_before:
                upset_record = {
                    'opponent': black_player,
                    'rating_diff': rating_diff,
                    'own_rating': white_rating_before,
                    'opponent_rating': black_rating_before,
                    'game_number': game_number,
                    'date': self.format_date(date_str) if date_str else None,
                    'result': 'Loss'
                }
                self.update_biggest_upsets(self.players[white_player], upset_record)
        
        # Update game counts
        self.players[white_player]['games'] += 1
        self.players[black_player]['games'] += 1
        
        # Update win/draw/loss stats
        if white_score == 1.0:
            self.players[white_player]['wins'] += 1
            self.players[black_player]['losses'] += 1
        elif black_score == 1.0:
            self.players[black_player]['wins'] += 1
            self.players[white_player]['losses'] += 1
        else:  # Draw
            self.players[white_player]['draws'] += 1
            self.players[black_player]['draws'] += 1
        
        # Update head-to-head records
        if black_player not in self.players[white_player]['opponents']:
            self.players[white_player]['opponents'][black_player] = {'games': 0, 'wins': 0, 'draws': 0, 'losses': 0}
        if white_player not in self.players[black_player]['opponents']:
            self.players[black_player]['opponents'][white_player] = {'games': 0, 'wins': 0, 'draws': 0, 'losses': 0}
        
        self.players[white_player]['opponents'][black_player]['games'] += 1
        self.players[black_player]['opponents'][white_player]['games'] += 1
        
        if white_score == 1.0:
            self.players[white_player]['opponents'][black_player]['wins'] += 1
            self.players[black_player]['opponents'][white_player]['losses'] += 1
        elif black_score == 1.0:
            self.players[white_player]['opponents'][black_player]['losses'] += 1
            self.players[black_player]['opponents'][white_player]['wins'] += 1
        else:
            self.players[white_player]['opponents'][black_player]['draws'] += 1
            self.players[black_player]['opponents'][white_player]['draws'] += 1
        
        # Store rating history for charting
        if 'rating_history' not in self.players[white_player]:
            self.players[white_player]['rating_history'] = []
        if 'rating_history' not in self.players[black_player]:
            self.players[black_player]['rating_history'] = []
        
        # Add current game to rating history
        game_date = self.format_date(date_str) if date_str else f"Game {game_number}"
        
        self.players[white_player]['rating_history'].append({
            'game': game_number,
            'date': game_date,
            'elo': self.players[white_player]['rating'],
            'glicko2': self.players[white_player]['glicko_rating'],
            'uscf': self.players[white_player]['uscf_rating']
        })
        
        self.players[black_player]['rating_history'].append({
            'game': game_number,
            'date': game_date,
            'elo': self.players[black_player]['rating'],
            'glicko2': self.players[black_player]['glicko_rating'],
            'uscf': self.players[black_player]['uscf_rating']
        })
        
        # Create game record
        game_record = {
            'game_number': game_number,
            'white_player': white_player,
            'black_player': black_player,
            'result': result,
            'date': self.format_date(date_str) if date_str else None,
            'date_raw': date_str,
            'white_rating_before': white_rating_before,
            'black_rating_before': black_rating_before,
            'white_rating_after': self.players[white_player]['rating'],
            'black_rating_after': self.players[black_player]['rating'],
            'white_change': white_change,
            'black_change': black_change
        }
        
        self.games.append(game_record)
        return game_record
    
    def load_games_file(self, filename):
        """Load and process games from file"""
        try:
            with open(filename, 'r') as f:
                lines = f.readlines()
            
            game_number = 1
            for line in lines:
                if self.process_game(line, game_number) is not None:
                    game_number += 1
            self.flush_uscf_event()  # rate the last event
            
            print(f"Processed {len(self.games)} games for {len(self.players)} players")
            
        except FileNotFoundError:
            print(f"Error: Could not find file '{filename}'")
            return False
        except Exception as e:
            print(f"Error processing games file: {e}")
            return False
        
        return True
    
    def build_tournaments(self):
        """Group dated games into tournaments (one per date) with round-by-round crosstables.
        
        Games are entered in chronological order, round by round, so a new round
        starts whenever a player would appear a second time in the current round.
        Tournaments are returned newest first.
        """
        # Each entry is (sort_key, players_involved, game_or_bye). Byes sort just
        # before the game that follows them in the file.
        by_date = {}
        for game in self.games:
            if game['date_raw']:
                by_date.setdefault(game['date_raw'], []).append(
                    ((game['game_number'], 1), (game['white_player'], game['black_player']), game))
        for bye in self.byes:
            if bye['date_raw']:
                by_date.setdefault(bye['date_raw'], []).append(
                    ((bye['sequence'], 0), (bye['player'],), bye))
        
        tournaments = []
        for date_raw in sorted(by_date, reverse=True):
            entries = sorted(by_date[date_raw], key=lambda e: e[0])
            rounds = []
            current, seen = [], set()
            for _, involved, entry in entries:
                if any(name in seen for name in involved):
                    rounds.append(current)
                    current, seen = [], set()
                current.append(entry)
                seen.update(involved)
            rounds.append(current)
            
            standings = {}
            def player_row(name):
                if name not in standings:
                    standings[name] = {
                        'name': name, 'points': 0.0,
                        'wins': 0, 'draws': 0, 'losses': 0,
                        'rounds': [None] * len(rounds)
                    }
                return standings[name]
            
            def record(name, round_index, cell):
                row = player_row(name)
                row['rounds'][round_index] = cell
                row['points'] += cell['score']
                if cell['score'] == 1:
                    row['wins'] += 1
                elif cell['score'] == 0.5:
                    row['draws'] += 1
                else:
                    row['losses'] += 1
            
            game_count = 0
            for round_index, round_entries in enumerate(rounds):
                for entry in round_entries:
                    if 'white_player' not in entry:
                        record(entry['player'], round_index,
                               {'opponent': 'BYE', 'color': None, 'score': 1.0, 'bye': True})
                        continue
                    game_count += 1
                    white_score, black_score = self.parse_game_result(entry['result'])
                    record(entry['white_player'], round_index,
                           {'opponent': entry['black_player'], 'color': 'W', 'score': white_score})
                    record(entry['black_player'], round_index,
                           {'opponent': entry['white_player'], 'color': 'B', 'score': black_score})
            
            self.add_tiebreaks(standings)
            
            tournaments.append({
                'date': self.format_date(date_raw),
                'date_raw': date_raw,
                'rounds': len(rounds),
                'games': game_count,
                'players': len(standings),
                'standings': sorted(standings.values(),
                                    key=lambda r: (-r['points'], -r['median'], -r['solkoff'],
                                                   -r['cumulative'], -r['cumulative_opp'], r['name']))
            })
        
        return tournaments
    
    def add_tiebreaks(self, standings):
        """Add USCF tiebreaks to each standing row, matching Coronate's implementation.
        
        Byes are excluded everywhere: a bye is not an opponent, and the bye round
        is skipped when accumulating the running score.
        """
        def real_games(row):
            return [cell for cell in row['rounds'] if cell and not cell.get('bye')]
        
        def opponent_scores(row):
            return [standings[cell['opponent']]['points'] for cell in real_games(row)]
        
        for row in standings.values():
            scores = sorted(opponent_scores(row))
            row['median'] = sum(scores[1:-1])  # USCF 34E1: drop highest and lowest
            row['solkoff'] = sum(scores)       # USCF 34E2
            running, cumulative = 0.0, 0.0     # USCF 34E3
            for cell in real_games(row):
                running += cell['score']
                cumulative += running
            row['cumulative'] = cumulative
        
        for row in standings.values():         # USCF 34E4
            row['cumulative_opp'] = sum(standings[cell['opponent']]['cumulative'] for cell in real_games(row))
    
    def compute_player_stats(self):
        """Attach tournament results, win streaks and colour split to each player."""
        for player in self.players.values():
            player['tournament_stats'] = {'played': 0, 'wins': 0, 'podiums': 0,
                                          'avg_finish': None, 'best': None, 'results': []}
            player['streaks'] = {'current': 0, 'longest': 0}
            player['color_stats'] = {'white': {'games': 0, 'points': 0.0},
                                     'black': {'games': 0, 'points': 0.0}}
        
        # Tournament results (tournaments come newest first)
        for tournament in self.build_tournaments():
            for place, row in enumerate(tournament['standings'], 1):
                rounds_played = sum(1 for cell in row['rounds'] if cell)
                stats = self.players[row['name']]['tournament_stats']
                stats['results'].append({
                    'date': tournament['date'], 'date_raw': tournament['date_raw'],
                    'place': place, 'players': tournament['players'],
                    'points': row['points'], 'rounds': rounds_played,
                    'wins': row['wins'], 'draws': row['draws'], 'losses': row['losses']
                })
        
        for player in self.players.values():
            stats = player['tournament_stats']
            results = stats['results']
            stats['played'] = len(results)
            stats['wins'] = sum(1 for r in results if r['place'] == 1)
            stats['podiums'] = sum(1 for r in results if r['place'] <= 3)
            if results:
                stats['avg_finish'] = round(sum(r['place'] for r in results) / len(results), 2)
                # Highest score percentage; ties go to the more recent tournament
                best = max(results, key=lambda r: r['points'] / r['rounds'])
                stats['best'] = {'date': best['date'], 'points': best['points'], 'rounds': best['rounds']}
        
        # Streaks and colour split, in game order
        for game in self.games:
            white_score, black_score = self.parse_game_result(game['result'])
            for name, color, score in ((game['white_player'], 'white', white_score),
                                       (game['black_player'], 'black', black_score)):
                player = self.players[name]
                player['color_stats'][color]['games'] += 1
                player['color_stats'][color]['points'] += score
                streaks = player['streaks']
                streaks['current'] = streaks['current'] + 1 if score == 1 else 0
                streaks['longest'] = max(streaks['longest'], streaks['current'])
    
    PAGES = [
        ('index.html', 'Ratings'),
        ('tournaments.html', 'Tournaments'),
        ('games.html', 'Games'),
    ]
    
    def nav_html(self, active):
        def link(name, label):
            css_class = ' class="active"' if name == active else ''
            return f'<a href="{name}"{css_class}>{label}</a>'
        links = ''.join(link(name, label) for name, label in self.PAGES)
        return f'<nav class="site-nav">{links}</nav>'
    
    def render_page(self, active, body, data_js, init_js):
        """Wrap a page body in the shared shell: styles, header, nav and scripts."""
        return f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>OU Chess Club Ratings</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body {{
            font-family: Arial, sans-serif;
            margin: 20px;
            background-color: #f5f5f5;
        }}
        
        .container {{
            max-width: 1200px;
            margin: 0 auto;
            background-color: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        
        h1, h2 {{
            color: #333;
            text-align: center;
        }}
        
        .section {{
            margin: 30px 0;
        }}
        
        .site-nav {{
            display: flex;
            justify-content: center;
            gap: 10px;
            margin: 10px 0 20px;
        }}
        
        .site-nav a {{
            padding: 8px 20px;
            border-radius: 4px;
            color: #2196F3;
            text-decoration: none;
            font-weight: bold;
        }}
        
        .site-nav a:hover {{
            background-color: #e3f2fd;
        }}
        
        .site-nav a.active {{
            background-color: #4CAF50;
            color: white;
        }}
        
        table {{
            width: 100%;
            border-collapse: collapse;
            margin: 20px 0;
        }}
        
        th, td {{
            border: 1px solid #ddd;
            padding: 12px;
            text-align: left;
        }}
        
        th {{
            background-color: #4CAF50;
            color: white;
            cursor: pointer;
            user-select: none;
        }}
        
        th:hover {{
            background-color: #45a049;
        }}
        
        tr:nth-child(even) {{
            background-color: #f9f9f9;
        }}
        
        tr:hover {{
            background-color: #f5f5f5;
        }}
        
        .player-name {{
            cursor: pointer;
            color: #2196F3;
            text-decoration: underline;
        }}
        
        .player-name:hover {{
            color: #0c7cd5;
        }}
        
        .filter-container {{
            margin: 20px 0;
            display: flex;
            gap: 20px;
            align-items: center;
            flex-wrap: wrap;
        }}
        
        .filter-container label {{
            font-weight: bold;
        }}
        
        .filter-container input, .filter-container select {{
            padding: 8px;
            border: 1px solid #ddd;
            border-radius: 4px;
        }}
        
        .filter-container button {{
            background-color: #4CAF50;
            color: white;
            padding: 8px 16px;
            border: none;
            border-radius: 4px;
            cursor: pointer;
        }}
        
        .filter-container button:hover {{
            background-color: #45a049;
        }}
        
        .hidden {{
            display: none;
        }}
        
        .tournament {{
            margin: 10px 0;
        }}
        
        .tournament summary {{
            cursor: pointer;
            font-weight: bold;
            font-size: 1.1em;
            padding: 10px;
            background-color: #f0f0f0;
            border-radius: 4px;
        }}
        
        .crosstable th {{
            cursor: default;
        }}
        
        .crosstable .no-game {{
            color: #999;
            text-align: center;
        }}
        
        .crosstable .bye {{
            font-style: italic;
        }}
        
        .crosstable .tiebreak {{
            color: #666;
        }}
        
        #tournament-results th {{
            cursor: default;
        }}
        
        .sort-indicator {{
            margin-left: 5px;
        }}
        
        .stats-summary {{
            background-color: #f0f8ff;
            padding: 15px;
            border-radius: 5px;
            margin: 20px 0;
        }}
        
        .chart-container {{
            background-color: white;
            padding: 20px;
            border-radius: 5px;
            margin: 20px 0;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        
        .chart-container canvas {{
            max-height: 400px;
        }}
        
        .stats-summary a {{
            color: #2196F3;
            text-decoration: none;
        }}
        
        .stats-summary a:hover {{
            color: #0c7cd5;
            text-decoration: underline;
        }}
        
        .ratings-grid {{
            display: grid;
            grid-template-columns: 1fr 1fr 1fr;
            gap: 20px;
            margin: 15px 0;
        }}
        
        .rating-column {{
            padding: 10px;
            border: 1px solid #ddd;
            border-radius: 5px;
            background-color: #f9f9f9;
        }}
        
        .rating-column h4 {{
            margin: 0 0 10px 0;
            color: #333;
            font-size: 14px;
            font-weight: bold;
        }}
        
        .rating-value {{
            font-weight: bold;
            margin: 5px 0;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>OU Chess Club Ratings</h1>
        {self.nav_html(active)}
        
{body}
    </div>

    <script>
        // Embedded data from Python
{data_js}
        let currentSort = {{ table: '', column: -1, ascending: true }};
        
        // HTML escape function to prevent XSS
        function escapeHtml(text) {{
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }}
        
        // A clickable player name: opens the profile in place on the ratings page,
        // or links to it from any other page.
        function playerLink(name) {{
            if (Object.prototype.hasOwnProperty.call(players, name)) {{
                const span = document.createElement('span');
                span.className = 'player-name';
                span.textContent = name;
                span.onclick = () => showPlayerView(name);
                return span;
            }}
            const link = document.createElement('a');
            link.className = 'player-name';
            link.textContent = name;
            link.href = 'index.html?player=' + encodeURIComponent(name);
            return link;
        }}
        
        // Open a profile from ?player=NAME. The value is only ever used as a lookup
        // key into the embedded players object; unknown names are ignored.
        function openPlayerFromUrl() {{
            const name = new URLSearchParams(window.location.search).get('player');
            if (name !== null && Object.prototype.hasOwnProperty.call(players, name)) {{
                showPlayerView(name);
            }}
        }}
        
        // Populate ratings table
        function populateRatingsTable() {{
            const tbody = document.getElementById('ratings-tbody');
            tbody.innerHTML = '';
            
            const sortedPlayers = Object.values(players).sort((a, b) => b.rating - a.rating);
            
            sortedPlayers.forEach(player => {{
                const winRate = player.games > 0 ? ((player.wins + player.draws * 0.5) / player.games * 100).toFixed(1) + '%' : '0%';
                
                const row = tbody.insertRow();
                
                // Create cells securely using createElement
                row.insertCell().appendChild(playerLink(player.name));
                
                const eloCell = row.insertCell();
                eloCell.textContent = player.rating;
                
                const glickoCell = row.insertCell();
                glickoCell.textContent = `${{player.glicko_rating}}±${{player.glicko_deviation}}`;
                
                const uscfCell = row.insertCell();
                uscfCell.textContent = player.uscf_rating;
                
                const gamesCell = row.insertCell();
                gamesCell.textContent = player.games;
                
                const winsCell = row.insertCell();
                winsCell.textContent = player.wins;
                
                const drawsCell = row.insertCell();
                drawsCell.textContent = player.draws;
                
                const lossesCell = row.insertCell();
                lossesCell.textContent = player.losses;
                
                const winRateCell = row.insertCell();
                winRateCell.textContent = winRate;
            }});
        }}
        
        // Format a score like 0.5 -> ½, 2.5 -> 2½
        function formatScore(score) {{
            const whole = Math.floor(score);
            const half = score - whole === 0.5 ? '½' : '';
            return whole === 0 && half ? half : `${{whole}}${{half}}`;
        }}
        
        // Populate tournament crosstables (one collapsible block per date, newest first)
        function populateTournaments() {{
            const container = document.getElementById('tournaments-container');
            container.innerHTML = '';
            
            tournaments.forEach((tournament, index) => {{
                const details = document.createElement('details');
                details.className = 'tournament';
                details.open = index === 0;
                
                const summary = document.createElement('summary');
                summary.textContent = `${{tournament.date}} — ${{tournament.players}} players, ${{tournament.rounds}} rounds, ${{tournament.games}} games`;
                details.appendChild(summary);
                
                const table = document.createElement('table');
                table.className = 'crosstable';
                const headRow = table.createTHead().insertRow();
                const roundLabels = Array.from({{length: tournament.rounds}}, (_, i) => `R${{i + 1}}`);
                ['#', 'Player', ...roundLabels, 'Pts', 'MMed', 'Solk', 'Cum', 'CumOpp', 'W-D-L'].forEach(label => {{
                    const th = document.createElement('th');
                    th.textContent = label;
                    headRow.appendChild(th);
                }});
                
                const tbody = table.createTBody();
                tournament.standings.forEach((standing, rank) => {{
                    const row = tbody.insertRow();
                    row.insertCell().textContent = rank + 1;
                    
                    row.insertCell().appendChild(playerLink(standing.name));
                    
                    standing.rounds.forEach(game => {{
                        const cell = row.insertCell();
                        if (game && game.bye) {{
                            cell.textContent = `bye ${{formatScore(game.score)}}`;
                            cell.className = 'bye';
                        }} else if (game) {{
                            cell.textContent = `${{game.opponent}} (${{game.color}}) ${{formatScore(game.score)}}`;
                        }} else {{
                            cell.textContent = '—';
                            cell.className = 'no-game';
                        }}
                    }});
                    
                    row.insertCell().textContent = formatScore(standing.points);
                    ['median', 'solkoff', 'cumulative', 'cumulative_opp'].forEach(key => {{
                        const cell = row.insertCell();
                        cell.textContent = formatScore(standing[key]);
                        cell.className = 'tiebreak';
                    }});
                    row.insertCell().textContent = `${{standing.wins}}-${{standing.draws}}-${{standing.losses}}`;
                }});
                
                details.appendChild(table);
                container.appendChild(details);
            }});
        }}
        
        // Populate games table
        function populateGamesTable(filteredGames = null) {{
            const tbody = document.getElementById('games-tbody');
            tbody.innerHTML = '';
            
            const gamesToShow = filteredGames || games.slice().reverse();
            
            gamesToShow.forEach(game => {{
                const whiteChangeStr = game.white_change > 0 ? `+${{game.white_change}}` : `${{game.white_change}}`;
                const blackChangeStr = game.black_change > 0 ? `+${{game.black_change}}` : `${{game.black_change}}`;
                const dateStr = game.date || '';
                
                const row = tbody.insertRow();
                
                // Create cells securely
                const gameNumCell = row.insertCell();
                gameNumCell.textContent = game.game_number;
                
                const dateCell = row.insertCell();
                dateCell.textContent = dateStr;
                
                const whiteCell = row.insertCell();
                whiteCell.textContent = `${{game.white_player}} (${{game.white_rating_before}} → ${{game.white_rating_after}}) ${{whiteChangeStr}}`;
                
                const blackCell = row.insertCell();
                blackCell.textContent = `${{game.black_player}} (${{game.black_rating_before}} → ${{game.black_rating_after}}) ${{blackChangeStr}}`;
                
                const resultCell = row.insertCell();
                resultCell.textContent = game.result;
            }});
        }}
        
        // Sort ratings table
        function sortRatingsTable(columnIndex) {{
            const tbody = document.getElementById('ratings-tbody');
            const rows = Array.from(tbody.querySelectorAll('tr'));
            
            const ascending = currentSort.table === 'ratings' && currentSort.column === columnIndex ? !currentSort.ascending : true;
            currentSort = {{ table: 'ratings', column: columnIndex, ascending: ascending }};
            
            rows.sort((a, b) => {{
                let aVal = a.cells[columnIndex].textContent.trim();
                let bVal = b.cells[columnIndex].textContent.trim();
                
                // Handle numeric columns
                if (columnIndex > 0) {{
                    if (columnIndex === 2) {{ // Glicko-2 column - extract rating before ± symbol
                        aVal = parseFloat(aVal.split('±')[0]) || 0;
                        bVal = parseFloat(bVal.split('±')[0]) || 0;
                    }} else {{
                        aVal = parseFloat(aVal.replace('%', '').replace(/[^\\d.-]/g, '')) || 0;
                        bVal = parseFloat(bVal.replace('%', '').replace(/[^\\d.-]/g, '')) || 0;
                    }}
                    return ascending ? aVal - bVal : bVal - aVal;
                }}
                
                // Handle text columns
                return ascending ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
            }});
            
            tbody.innerHTML = '';
            rows.forEach(row => tbody.appendChild(row));
            
            updateSortIndicators('ratings', columnIndex, ascending);
        }}
        
        // Sort games table
        function sortGamesTable(columnIndex) {{
            const tbody = document.getElementById('games-tbody');
            const rows = Array.from(tbody.querySelectorAll('tr'));
            
            const ascending = currentSort.table === 'games' && currentSort.column === columnIndex ? !currentSort.ascending : true;
            currentSort = {{ table: 'games', column: columnIndex, ascending: ascending }};
            
            rows.sort((a, b) => {{
                let aVal = a.cells[columnIndex].textContent.trim();
                let bVal = b.cells[columnIndex].textContent.trim();
                
                if (columnIndex === 0) {{ // Game number column
                    aVal = parseInt(aVal) || 0;
                    bVal = parseInt(bVal) || 0;
                    return ascending ? aVal - bVal : bVal - aVal;
                }} else if (columnIndex === 1) {{ // Date column
                    // Handle empty dates by treating them as very old dates for sorting
                    const aDate = aVal === '' ? '1900-01-01' : aVal;
                    const bDate = bVal === '' ? '1900-01-01' : bVal;
                    return ascending ? aDate.localeCompare(bDate) : bDate.localeCompare(aDate);
                }}
                
                return ascending ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
            }});
            
            tbody.innerHTML = '';
            rows.forEach(row => tbody.appendChild(row));
            
            updateSortIndicators('games', columnIndex, ascending);
        }}
        
        // Sort head-to-head table
        function sortH2HTable(columnIndex) {{
            const tbody = document.getElementById('h2h-tbody');
            const rows = Array.from(tbody.querySelectorAll('tr'));
            
            const ascending = currentSort.table === 'h2h' && currentSort.column === columnIndex ? !currentSort.ascending : true;
            currentSort = {{ table: 'h2h', column: columnIndex, ascending: ascending }};
            
            rows.sort((a, b) => {{
                let aVal = a.cells[columnIndex].textContent.trim();
                let bVal = b.cells[columnIndex].textContent.trim();
                
                if (columnIndex === 0) {{ // Opponent name column (text)
                    return ascending ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
                }} else if (columnIndex === 5) {{ // Score column (fraction like "5.5/10")
                    const aParts = aVal.split('/');
                    const bParts = bVal.split('/');
                    const aScore = aParts.length === 2 ? parseFloat(aParts[0]) / parseFloat(aParts[1]) : 0;
                    const bScore = bParts.length === 2 ? parseFloat(bParts[0]) / parseFloat(bParts[1]) : 0;
                    return ascending ? aScore - bScore : bScore - aScore;
                }} else {{ // Numeric columns (Games, Wins, Draws, Losses)
                    const aNum = parseInt(aVal) || 0;
                    const bNum = parseInt(bVal) || 0;
                    return ascending ? aNum - bNum : bNum - aNum;
                }}
            }});
            
            tbody.innerHTML = '';
            rows.forEach(row => tbody.appendChild(row));
            
            updateSortIndicators('h2h', columnIndex, ascending);
        }}
        
        // Update sort indicators
        function updateSortIndicators(table, columnIndex, ascending) {{
            let tableId;
            if (table === 'ratings') {{
                tableId = 'ratings-table';
            }} else if (table === 'games') {{
                tableId = 'games-table';
            }} else if (table === 'h2h') {{
                tableId = 'h2h-table';
            }}
            
            const indicators = document.querySelectorAll(`#${{tableId}} .sort-indicator`);
            
            indicators.forEach((indicator, index) => {{
                if (index === columnIndex) {{
                    indicator.textContent = ascending ? '↑' : '↓';
                }} else {{
                    indicator.textContent = '';
                }}
            }});
        }}
        
        // Apply filters to games table
        function applyFilters() {{
            const playerFilter = document.getElementById('player-filter').value.toLowerCase();
            const resultFilter = document.getElementById('result-filter').value;
            
            let filteredGames = games.slice().reverse();
            
            if (playerFilter) {{
                filteredGames = filteredGames.filter(game => 
                    game.white_player.toLowerCase().includes(playerFilter) || 
                    game.black_player.toLowerCase().includes(playerFilter)
                );
            }}
            
            if (resultFilter) {{
                filteredGames = filteredGames.filter(game => game.result === resultFilter);
            }}
            
            populateGamesTable(filteredGames);
        }}
        
        // Clear filters
        function clearFilters() {{
            document.getElementById('player-filter').value = '';
            document.getElementById('result-filter').value = '';
            populateGamesTable();
        }}
        
        // Global variable to store current chart instance
        let currentChart = null;
        
        // Create rating history chart
        function createRatingChart(playerName) {{
            const player = players[playerName];
            if (!player || !player.rating_history || player.rating_history.length === 0) {{
                return;
            }}
            
            // Destroy existing chart if it exists
            if (currentChart) {{
                currentChart.destroy();
            }}
            
            const ctx = document.getElementById('rating-chart').getContext('2d');
            const history = player.rating_history;
            
            // Prepare data for chart
            const labels = history.map(h => h.date || `Game ${{h.game}}`);
            const eloData = history.map(h => h.elo);
            const glicko2Data = history.map(h => h.glicko2);
            const uscfData = history.map(h => h.uscf);
            
            currentChart = new Chart(ctx, {{
                type: 'line',
                data: {{
                    labels: labels,
                    datasets: [{{
                        label: 'ELO',
                        data: eloData,
                        borderColor: 'rgb(75, 192, 192)',
                        backgroundColor: 'rgba(75, 192, 192, 0.1)',
                        tension: 0.1,
                        fill: false
                    }}, {{
                        label: 'Glicko-2',
                        data: glicko2Data,
                        borderColor: 'rgb(255, 99, 132)',
                        backgroundColor: 'rgba(255, 99, 132, 0.1)',
                        tension: 0.1,
                        fill: false
                    }}, {{
                        label: 'USCF',
                        data: uscfData,
                        borderColor: 'rgb(54, 162, 235)',
                        backgroundColor: 'rgba(54, 162, 235, 0.1)',
                        tension: 0.1,
                        fill: false
                    }}]
                }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: true,
                    plugins: {{
                        title: {{
                            display: true,
                            text: `Rating History - ${{playerName}}`
                        }},
                        legend: {{
                            display: true,
                            position: 'top'
                        }}
                    }},
                    scales: {{
                        x: {{
                            display: true,
                            title: {{
                                display: true,
                                text: 'Games'
                            }},
                            ticks: {{
                                maxTicksLimit: 10
                            }}
                        }},
                        y: {{
                            display: true,
                            title: {{
                                display: true,
                                text: 'Rating'
                            }},
                            min: Math.min(...eloData, ...glicko2Data, ...uscfData) - 50,
                            max: Math.max(...eloData, ...glicko2Data, ...uscfData) + 50
                        }}
                    }},
                    interaction: {{
                        intersect: false,
                        mode: 'index'
                    }}
                }}
            }});
        }}
        
        // Show player detail view
        function ordinal(n) {{
            const suffix = (n % 100 >= 11 && n % 100 <= 13) ? 'th' : ['th', 'st', 'nd', 'rd'][n % 10] || 'th';
            return `${{n}}${{suffix}}`;
        }}
        
        function addStatLine(container, label, value) {{
            const p = document.createElement('p');
            const strong = document.createElement('strong');
            strong.textContent = `${{label}}: `;
            p.appendChild(strong);
            p.appendChild(document.createTextNode(value));
            container.appendChild(p);
        }}
        
        // Tournament summary, best result, streaks, colour split and per-tournament results
        function populateTournamentStats(player) {{
            const summary = document.getElementById('tournament-summary');
            summary.innerHTML = '';
            const stats = player.tournament_stats;
            const table = document.getElementById('tournament-results');
            const tbody = document.getElementById('tournament-results-tbody');
            tbody.innerHTML = '';
            
            if (stats.played === 0) {{
                summary.innerHTML = '<p>No tournaments played yet.</p>';
                table.classList.add('hidden');
                return;
            }}
            table.classList.remove('hidden');
            
            addStatLine(summary, 'Tournaments', `${{stats.played}} played · ${{stats.wins}} won · ${{stats.podiums}} podiums · avg finish ${{stats.avg_finish}}`);
            addStatLine(summary, 'Best result', `${{formatScore(stats.best.points)}}/${{stats.best.rounds}} on ${{stats.best.date}}`);
            addStatLine(summary, 'Win streak', `${{player.streaks.current}} current · ${{player.streaks.longest}} longest`);
            const white = player.color_stats.white, black = player.color_stats.black;
            addStatLine(summary, 'By colour', `White ${{formatScore(white.points)}}/${{white.games}} · Black ${{formatScore(black.points)}}/${{black.games}}`);
            
            stats.results.forEach(result => {{
                const row = tbody.insertRow();
                row.insertCell().textContent = result.date;
                row.insertCell().textContent = `${{ordinal(result.place)}} of ${{result.players}}`;
                row.insertCell().textContent = `${{formatScore(result.points)}}/${{result.rounds}}`;
                row.insertCell().textContent = `${{result.wins}}-${{result.draws}}-${{result.losses}}`;
            }});
        }}
        
        function showPlayerView(playerName) {{
            if (!Object.prototype.hasOwnProperty.call(players, playerName)) return;
            const player = players[playerName];
            
            document.getElementById('main-view').classList.add('hidden');
            document.getElementById('player-view').classList.remove('hidden');
            
            document.getElementById('player-title').textContent = playerName;
            
            const winRate = player.games > 0 ? ((player.wins + player.draws * 0.5) / player.games * 100).toFixed(1) + '%' : '0%';
            
            // Create player stats securely
            const playerStatsDiv = document.getElementById('player-stats');
            playerStatsDiv.innerHTML = ''; // Clear existing content
            
            const ratingP = document.createElement('p');
            ratingP.innerHTML = '<strong>Current Rating:</strong> ';
            ratingP.appendChild(document.createTextNode(player.rating));
            playerStatsDiv.appendChild(ratingP);
            
            const gamesP = document.createElement('p');
            gamesP.innerHTML = '<strong>Games Played:</strong> ';
            gamesP.appendChild(document.createTextNode(player.games));
            playerStatsDiv.appendChild(gamesP);
            
            const recordP = document.createElement('p');
            recordP.innerHTML = '<strong>Record:</strong> ';
            recordP.appendChild(document.createTextNode(`${{player.wins}}W - ${{player.draws}}D - ${{player.losses}}L`));
            playerStatsDiv.appendChild(recordP);
            
            const winRateP = document.createElement('p');
            winRateP.innerHTML = '<strong>Win Rate:</strong> ';
            winRateP.appendChild(document.createTextNode(winRate));
            playerStatsDiv.appendChild(winRateP);
            
            // Create head-to-head table securely
            const tbody = document.getElementById('h2h-tbody');
            tbody.innerHTML = '';
            
            const opponents = Object.keys(player.opponents).sort();
            opponents.forEach(opponentName => {{
                const record = player.opponents[opponentName];
                const score = record.wins + record.draws * 0.5;
                const scoreText = `${{score}}/${{record.games}}`;
                
                const row = tbody.insertRow();
                
                const opponentCell = row.insertCell();
                opponentCell.textContent = opponentName;
                
                const gamesCell = row.insertCell();
                gamesCell.textContent = record.games;
                
                const winsCell = row.insertCell();
                winsCell.textContent = record.wins;
                
                const drawsCell = row.insertCell();
                drawsCell.textContent = record.draws;
                
                const lossesCell = row.insertCell();
                lossesCell.textContent = record.losses;
                
                const scoreCell = row.insertCell();
                scoreCell.textContent = scoreText;
            }});
            
            // Populate current ratings section with three-column layout
            const currentRatingsDiv = document.getElementById('current-ratings');
            currentRatingsDiv.innerHTML = '';
            
            // Create grid container
            const ratingsGrid = document.createElement('div');
            ratingsGrid.className = 'ratings-grid';
            
            // Current ratings column
            const currentColumn = document.createElement('div');
            currentColumn.className = 'rating-column';
            const currentHeader = document.createElement('h4');
            currentHeader.textContent = 'Current';
            currentColumn.appendChild(currentHeader);
            
            const eloCurrentDiv = document.createElement('div');
            eloCurrentDiv.className = 'rating-value';
            eloCurrentDiv.innerHTML = '<strong><a href="https://handbook.fide.com/chapter/B022024" target="_blank" rel="noopener">ELO</a>:</strong> ' + player.rating;
            currentColumn.appendChild(eloCurrentDiv);
            
            const glickoCurrentDiv = document.createElement('div');
            glickoCurrentDiv.className = 'rating-value';
            glickoCurrentDiv.innerHTML = '<strong><a href="http://www.glicko.net/glicko/glicko2.pdf" target="_blank" rel="noopener">Glicko-2</a>:</strong> ' + player.glicko_rating + '±' + player.glicko_deviation;
            currentColumn.appendChild(glickoCurrentDiv);
            
            const uscfCurrentDiv = document.createElement('div');
            uscfCurrentDiv.className = 'rating-value';
            uscfCurrentDiv.innerHTML = '<strong><a href="https://www.glicko.net/ratings/rating.system.pdf" target="_blank" rel="noopener">USCF</a>:</strong> ' + player.uscf_rating;
            currentColumn.appendChild(uscfCurrentDiv);
            
            // Highest ratings column
            const highestColumn = document.createElement('div');
            highestColumn.className = 'rating-column';
            const highestHeader = document.createElement('h4');
            highestHeader.textContent = 'Highest';
            highestColumn.appendChild(highestHeader);
            
            const eloHighestDiv = document.createElement('div');
            eloHighestDiv.className = 'rating-value';
            eloHighestDiv.innerHTML = '<strong>ELO:</strong> ' + player.highest_elo;
            highestColumn.appendChild(eloHighestDiv);
            
            const glickoHighestDiv = document.createElement('div');
            glickoHighestDiv.className = 'rating-value';
            glickoHighestDiv.innerHTML = '<strong>Glicko-2:</strong> ' + player.highest_glicko;
            highestColumn.appendChild(glickoHighestDiv);
            
            const uscfHighestDiv = document.createElement('div');
            uscfHighestDiv.className = 'rating-value';
            uscfHighestDiv.innerHTML = '<strong>USCF:</strong> ' + player.highest_uscf;
            highestColumn.appendChild(uscfHighestDiv);
            
            // Lowest ratings column
            const lowestColumn = document.createElement('div');
            lowestColumn.className = 'rating-column';
            const lowestHeader = document.createElement('h4');
            lowestHeader.textContent = 'Lowest';
            lowestColumn.appendChild(lowestHeader);
            
            const eloLowestDiv = document.createElement('div');
            eloLowestDiv.className = 'rating-value';
            eloLowestDiv.innerHTML = '<strong>ELO:</strong> ' + player.lowest_elo;
            lowestColumn.appendChild(eloLowestDiv);
            
            const glickoLowestDiv = document.createElement('div');
            glickoLowestDiv.className = 'rating-value';
            glickoLowestDiv.innerHTML = '<strong>Glicko-2:</strong> ' + player.lowest_glicko;
            lowestColumn.appendChild(glickoLowestDiv);
            
            const uscfLowestDiv = document.createElement('div');
            uscfLowestDiv.className = 'rating-value';
            uscfLowestDiv.innerHTML = '<strong>USCF:</strong> ' + player.lowest_uscf;
            lowestColumn.appendChild(uscfLowestDiv);
            
            // Add columns to grid
            ratingsGrid.appendChild(currentColumn);
            ratingsGrid.appendChild(highestColumn);
            ratingsGrid.appendChild(lowestColumn);
            
            // Add grid to container
            currentRatingsDiv.appendChild(ratingsGrid);
            
            populateTournamentStats(player);
            
            // Populate biggest wins section
            const biggestWinsDiv = document.getElementById('biggest-wins');
            biggestWinsDiv.innerHTML = '';
            
            if (player.biggest_wins && player.biggest_wins.length > 0) {{
                player.biggest_wins.forEach((win, index) => {{
                    const winDiv = document.createElement('div');
                    winDiv.style.marginBottom = '10px';
                    winDiv.innerHTML = `
                        <strong>${{index + 1}}. vs ${{win.opponent}}</strong><br>
                        Rating difference: +${{win.rating_diff}} (${{win.own_rating}} vs ${{win.opponent_rating}})<br>
                        Game #${{win.game_number}}${{win.date ? ', ' + win.date : ''}}
                    `;
                    biggestWinsDiv.appendChild(winDiv);
                }});
            }} else {{
                biggestWinsDiv.innerHTML = '<p>No significant wins recorded yet.</p>';
            }}
            
            // Populate biggest upsets section
            const biggestUpsetsDiv = document.getElementById('biggest-upsets');
            biggestUpsetsDiv.innerHTML = '';
            
            if (player.biggest_upsets && player.biggest_upsets.length > 0) {{
                player.biggest_upsets.forEach((upset, index) => {{
                    const upsetDiv = document.createElement('div');
                    upsetDiv.style.marginBottom = '10px';
                    upsetDiv.innerHTML = `
                        <strong>${{index + 1}}. Lost to ${{upset.opponent}}</strong><br>
                        Rating difference: -${{upset.rating_diff}} (${{upset.own_rating}} vs ${{upset.opponent_rating}})<br>
                        Game #${{upset.game_number}}${{upset.date ? ', ' + upset.date : ''}}
                    `;
                    biggestUpsetsDiv.appendChild(upsetDiv);
                }});
            }} else {{
                biggestUpsetsDiv.innerHTML = '<p>No major upsets recorded.</p>';
            }}
            
            // Create the rating chart after populating the table
            createRatingChart(playerName);
        }}
        
        
        // Initialize the page
        window.addEventListener('DOMContentLoaded', function() {{
{init_js}
        }});
    </script>
</body>
</html>'''
    
    def generate_html(self, output_dir='.'):
        """Generate index.html, tournaments.html and games.html with embedded data"""
        self.compute_player_stats()
        
        def data_js(players='{}', games='[]', tournaments='[]'):
            return (f"        let players = {players};\n"
                    f"        let games = {games};\n"
                    f"        let tournaments = {tournaments};")
        
        pages = {
            'index.html': self.render_page(
                'index.html', RATINGS_BODY, data_js(players=json.dumps(self.players)),
                "            populateRatingsTable();\n            openPlayerFromUrl();"),
            'tournaments.html': self.render_page(
                'tournaments.html', TOURNAMENTS_BODY, data_js(tournaments=json.dumps(self.build_tournaments())),
                "            populateTournaments();"),
            'games.html': self.render_page(
                'games.html', GAMES_BODY, data_js(games=json.dumps(self.games)),
                "            populateGamesTable();"),
        }
        
        try:
            for filename, html in pages.items():
                output_path = os.path.join(output_dir, filename)
                with open(output_path, 'w') as f:
                    f.write(html)
                print(f"Generated HTML file: {output_path}")
            print(f"Open file://{os.path.abspath(os.path.join(output_dir, 'index.html'))} in your browser")
            return True
        except Exception as e:
            print(f"Error writing HTML files: {e}")
            return False



# Page bodies (plain strings; no f-string braces inside)
RATINGS_BODY = '''        <!-- Main Ratings View -->
        <div id="main-view">
            <div class="section">
                <h2>Player Ratings</h2>
                <table id="ratings-table">
                    <thead>
                        <tr>
                            <th onclick="sortRatingsTable(0)">Player Name <span class="sort-indicator"></span></th>
                            <th onclick="sortRatingsTable(1)">ELO <span class="sort-indicator"></span></th>
                            <th onclick="sortRatingsTable(2)">Glicko-2 <span class="sort-indicator"></span></th>
                            <th onclick="sortRatingsTable(3)">USCF <span class="sort-indicator"></span></th>
                            <th onclick="sortRatingsTable(4)">Games Played <span class="sort-indicator"></span></th>
                            <th onclick="sortRatingsTable(5)">Wins <span class="sort-indicator"></span></th>
                            <th onclick="sortRatingsTable(6)">Draws <span class="sort-indicator"></span></th>
                            <th onclick="sortRatingsTable(7)">Losses <span class="sort-indicator"></span></th>
                            <th onclick="sortRatingsTable(8)">Win Rate <span class="sort-indicator"></span></th>
                        </tr>
                    </thead>
                    <tbody id="ratings-tbody">
                    </tbody>
                </table>
            </div>
        </div>
        
        <!-- Player Detail View -->
        <div id="player-view" class="hidden">
            <h2 id="player-title"></h2>
            <div id="player-stats" class="stats-summary"></div>
            
            <div class="chart-container">
                <h3>Rating History</h3>
                <canvas id="rating-chart"></canvas>
            </div>
            
            <div class="stats-summary">
                <h3>Ratings</h3>
                <div id="current-ratings"></div>
            </div>
            
            <div class="stats-summary">
                <h3>Tournaments</h3>
                <div id="tournament-summary"></div>
                <table id="tournament-results">
                    <thead>
                        <tr>
                            <th>Date</th>
                            <th>Place</th>
                            <th>Score</th>
                            <th>W-D-L</th>
                        </tr>
                    </thead>
                    <tbody id="tournament-results-tbody"></tbody>
                </table>
            </div>
            
            <div class="stats-summary">
                <h3>Biggest Wins (Against Higher-Rated Players)</h3>
                <div id="biggest-wins"></div>
            </div>
            
            <div class="stats-summary">
                <h3>Biggest Upsets (Losses to Lower-Rated Players)</h3>
                <div id="biggest-upsets"></div>
            </div>
            
            <h3>Head-to-Head Records</h3>
            <table id="h2h-table">
                <thead>
                    <tr>
                        <th onclick="sortH2HTable(0)">Opponent <span class="sort-indicator"></span></th>
                        <th onclick="sortH2HTable(1)">Games <span class="sort-indicator"></span></th>
                        <th onclick="sortH2HTable(2)">Wins <span class="sort-indicator"></span></th>
                        <th onclick="sortH2HTable(3)">Draws <span class="sort-indicator"></span></th>
                        <th onclick="sortH2HTable(4)">Losses <span class="sort-indicator"></span></th>
                        <th onclick="sortH2HTable(5)">Score <span class="sort-indicator"></span></th>
                    </tr>
                </thead>
                <tbody id="h2h-tbody">
                </tbody>
            </table>
        </div>'''

TOURNAMENTS_BODY = '''            <div class="section">
                <h2>Tournaments</h2>
                <div id="tournaments-container"></div>
            </div>'''

GAMES_BODY = '''            <div class="section">
                <h2>Game History</h2>
                <div class="filter-container">
                    <label for="player-filter">Filter by Player:</label>
                    <input type="text" id="player-filter" placeholder="Enter player name">
                    
                    <label for="result-filter">Filter by Result:</label>
                    <select id="result-filter">
                        <option value="">All Results</option>
                        <option value="1-0">White Wins</option>
                        <option value="0-1">Black Wins</option>
                        <option value="0.5-0.5">Draw</option>
                    </select>
                    
                    <button onclick="applyFilters()">Apply Filters</button>
                    <button onclick="clearFilters()">Clear Filters</button>
                </div>
                
                <table id="games-table">
                    <thead>
                        <tr>
                            <th onclick="sortGamesTable(0)">Game # <span class="sort-indicator"></span></th>
                            <th onclick="sortGamesTable(1)">Date <span class="sort-indicator"></span></th>
                            <th onclick="sortGamesTable(2)">White Player <span class="sort-indicator"></span></th>
                            <th onclick="sortGamesTable(3)">Black Player <span class="sort-indicator"></span></th>
                            <th onclick="sortGamesTable(4)">Result <span class="sort-indicator"></span></th>
                        </tr>
                    </thead>
                    <tbody id="games-tbody">
                    </tbody>
                </table>
            </div>'''


def main():
    # Initialize the rating system
    rating_system = ChessRatingSystem()
    
    # Load games from file
    if rating_system.load_games_file('games.txt'):
        # Generate HTML output
        rating_system.generate_html('.')
        
        # Print summary
        print(f"\\nRating Summary:")
        print(f"Total players: {len(rating_system.players)}")
        print(f"Total games: {len(rating_system.games)}")
        
        print("\\nTop 5 Players:")
        sorted_players = sorted(rating_system.players.values(), key=lambda p: p['rating'], reverse=True)
        for i, player in enumerate(sorted_players[:5], 1):
            winrate = (player['wins'] + player['draws'] * 0.5) / player['games'] * 100 if player['games'] > 0 else 0
            print(f"{i}. {player['name']}: {player['rating']} ({player['wins']}-{player['draws']}-{player['losses']}, {winrate:.1f}%)")


if __name__ == "__main__":
    main()