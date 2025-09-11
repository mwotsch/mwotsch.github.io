#!/usr/bin/env python3
"""
Chess Rating System - Generates HTML rating tables from game data
Implements ELO rating system with interactive HTML output
"""

import math
import json
import os


class ChessRatingSystem:
    def __init__(self, k_factor=32, initial_rating=1200):
        self.k_factor = k_factor
        self.initial_rating = initial_rating
        self.players = {}
        self.games = []
    
    def init_player(self, name):
        """Initialize a new player with default stats"""
        if name not in self.players:
            self.players[name] = {
                'name': name,
                'rating': self.initial_rating,
                'glicko_rating': 1200,  # Glicko-2 starting at 1200
                'glicko_deviation': 350,  # Initial rating deviation
                'glicko_volatility': 0.06,  # Initial volatility
                'uscf_rating': 1200,  # USCF starting rating
                'uscf_games': 0,  # Track games for USCF K-factor adjustment
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
                'highest_uscf': 1200,
                'lowest_uscf': 1200
            }
    
    def calculate_elo_change(self, rating_a, rating_b, score_a):
        """Calculate ELO rating change for player A"""
        expected_a = 1 / (1 + math.pow(10, (rating_b - rating_a) / 400))
        return round(self.k_factor * (score_a - expected_a))
    
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
        return 1 / (1 + math.exp(-self.glicko2_g(phi_j) * (mu - mu_j)))
    
    def get_uscf_k_factor(self, player):
        """Get USCF K-factor based on games played and rating"""
        if player['uscf_games'] < 20:
            return 40  # Provisional rating period
        elif player['uscf_rating'] < 2100:
            return 32  # Regular players
        else:
            return 24  # Masters and above
    
    def calculate_uscf_change(self, rating_a, rating_b, score_a, k_factor):
        """Calculate USCF rating change for player A"""
        expected_a = 1 / (1 + math.pow(10, (rating_b - rating_a) / 400))
        return round(k_factor * (score_a - expected_a))
    
    def update_uscf_ratings(self, player_a, player_b, score_a):
        """Update USCF ratings for both players"""
        # Get K-factors
        k_a = self.get_uscf_k_factor(player_a)
        k_b = self.get_uscf_k_factor(player_b)
        
        # Calculate changes
        change_a = self.calculate_uscf_change(player_a['uscf_rating'], player_b['uscf_rating'], score_a, k_a)
        change_b = self.calculate_uscf_change(player_b['uscf_rating'], player_a['uscf_rating'], 1 - score_a, k_b)
        
        # Update ratings
        player_a['uscf_rating'] += change_a
        player_b['uscf_rating'] += change_b
        
        # Update game counts for K-factor tracking
        player_a['uscf_games'] += 1
        player_b['uscf_games'] += 1
    
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
        
        # USCF extremes
        if player['uscf_rating'] > player['highest_uscf']:
            player['highest_uscf'] = player['uscf_rating']
        if player['uscf_rating'] < player['lowest_uscf']:
            player['lowest_uscf'] = player['uscf_rating']
    
    def update_glicko2_ratings(self, player_a, player_b, score_a):
        """Update Glicko-2 ratings for both players"""
        # Convert to Glicko-2 scale
        mu_a = self.glicko2_scale(player_a['glicko_rating'])
        mu_b = self.glicko2_scale(player_b['glicko_rating'])
        phi_a = player_a['glicko_deviation'] / 173.7178
        phi_b = player_b['glicko_deviation'] / 173.7178
        sigma_a = player_a['glicko_volatility']
        sigma_b = player_b['glicko_volatility']
        
        # System constant
        tau = 0.5
        
        # Update player A
        g_b = self.glicko2_g(phi_b)
        e_ab = self.glicko2_e(mu_a, mu_b, phi_b)
        
        v_a = 1 / (g_b * g_b * e_ab * (1 - e_ab))
        delta_a = v_a * g_b * (score_a - e_ab)
        
        # Update volatility (simplified)
        sigma_a_new = math.sqrt((sigma_a * sigma_a + delta_a * delta_a / v_a) / 2)
        sigma_a_new = min(sigma_a_new, 0.2)  # Cap volatility
        
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
        
        sigma_b_new = math.sqrt((sigma_b * sigma_b + delta_b * delta_b / v_b) / 2)
        sigma_b_new = min(sigma_b_new, 0.2)
        
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
        
        # Calculate rating changes
        white_change = self.calculate_elo_change(white_rating_before, black_rating_before, white_score)
        black_change = self.calculate_elo_change(black_rating_before, white_rating_before, black_score)
        
        # Update ratings
        self.players[white_player]['rating'] += white_change
        self.players[black_player]['rating'] += black_change
        
        # Update Glicko-2 ratings
        self.update_glicko2_ratings(self.players[white_player], self.players[black_player], white_score)
        
        # Update USCF ratings
        self.update_uscf_ratings(self.players[white_player], self.players[black_player], white_score)
        
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
            
            for i, line in enumerate(lines, 1):
                self.process_game(line, i)
            
            print(f"Processed {len(self.games)} games for {len(self.players)} players")
            
        except FileNotFoundError:
            print(f"Error: Could not find file '{filename}'")
            return False
        except Exception as e:
            print(f"Error processing games file: {e}")
            return False
        
        return True
    
    def generate_html(self, output_filename='index.html'):
        """Generate HTML file with embedded data"""
        
        # Prepare data for JavaScript
        players_data = json.dumps(self.players)
        games_data = json.dumps(self.games)
        
        html_content = f'''<!DOCTYPE html>
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
        
        .back-button {{
            background-color: #2196F3;
            color: white;
            padding: 10px 20px;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            margin: 20px 0;
        }}
        
        .back-button:hover {{
            background-color: #0c7cd5;
        }}
        
        .hidden {{
            display: none;
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
        
        <!-- Main Ratings View -->
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
            
            <div class="section">
                <h2>Game History</h2>
                <div class="filter-container">
                    <label for="player-filter">Filter by Player:</label>
                    <input type="text" id="player-filter" placeholder="Enter player name">
                    
                    <label for="result-filter">Filter by Result:</label>
                    <select id="result-filter">
                        <option value="">All Results</option>
                        <option value="1:0">White Wins</option>
                        <option value="0:1">Black Wins</option>
                        <option value="0.5:0.5">Draw</option>
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
            </div>
        </div>
        
        <!-- Player Detail View -->
        <div id="player-view" class="hidden">
            <button class="back-button" onclick="showMainView()">← Back to Main View</button>
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
        </div>
    </div>

    <script>
        // Embedded data from Python
        let players = {players_data};
        let games = {games_data};
        let currentSort = {{ table: '', column: -1, ascending: true }};
        
        // HTML escape function to prevent XSS
        function escapeHtml(text) {{
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
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
                const nameCell = row.insertCell();
                const nameSpan = document.createElement('span');
                nameSpan.className = 'player-name';
                nameSpan.textContent = player.name;
                nameSpan.onclick = () => showPlayerView(player.name);
                nameCell.appendChild(nameSpan);
                
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
        function showPlayerView(playerName) {{
            const player = players[playerName];
            if (!player) return;
            
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
            eloCurrentDiv.innerHTML = '<strong><a href="https://en.wikipedia.org/wiki/Elo_rating_system" target="_blank" rel="noopener">ELO</a>:</strong> ' + player.rating;
            currentColumn.appendChild(eloCurrentDiv);
            
            const glickoCurrentDiv = document.createElement('div');
            glickoCurrentDiv.className = 'rating-value';
            glickoCurrentDiv.innerHTML = '<strong><a href="http://www.glicko.net/glicko/glicko2.pdf" target="_blank" rel="noopener">Glicko-2</a>:</strong> ' + player.glicko_rating + '±' + player.glicko_deviation;
            currentColumn.appendChild(glickoCurrentDiv);
            
            const uscfCurrentDiv = document.createElement('div');
            uscfCurrentDiv.className = 'rating-value';
            uscfCurrentDiv.innerHTML = '<strong><a href="https://new.uschess.org/sites/default/files/media/documents/the-us-chess-rating-system-revised-september-2020.pdf" target="_blank" rel="noopener">USCF</a>:</strong> ' + player.uscf_rating;
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
        
        // Show main view
        function showMainView() {{
            document.getElementById('player-view').classList.add('hidden');
            document.getElementById('main-view').classList.remove('hidden');
        }}
        
        // Initialize the application
        window.addEventListener('DOMContentLoaded', function() {{
            populateRatingsTable();
            populateGamesTable();
        }});
    </script>
</body>
</html>'''
        
        try:
            with open(output_filename, 'w') as f:
                f.write(html_content)
            print(f"Generated HTML file: {output_filename}")
            print(f"Open file://{os.path.abspath(output_filename)} in your browser")
            return True
        except Exception as e:
            print(f"Error writing HTML file: {e}")
            return False


def main():
    # Initialize the rating system
    rating_system = ChessRatingSystem()
    
    # Load games from file
    if rating_system.load_games_file('games.txt'):
        # Generate HTML output
        rating_system.generate_html('index.html')
        
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