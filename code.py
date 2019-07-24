import os
import time
import numpy as np
import pandas as pd
import subprocess
import argparse

def print_possession(possession):
	print('----------------------------------------------------------------')
	print('Team with possession: {0}'.format(possession['team']))
	for play in possession['plays']:
		event, action = play['Event_Msg_Type'], play['Action_Type']
		if event_codes[event][action]['activity_description']:
			print(event_codes[event][action]['event_description'] + ': ' + event_codes[event][action]['activity_description'])
		elif event == 8:
			print(event_codes[event][action]['event_description'] + ': ' + play['Person2'])
		else:
			print(event_codes[event][action]['event_description'])
	print('----------------------------------------------------------------')

# Import datasets
event_codes = pd.read_csv('data/Event_Codes.txt', sep='\t')
game_lineup = pd.read_csv('data/Game_Lineup.txt', sep='\t')
play_by_play = pd.read_csv('data/Play_by_Play.txt', sep='\t')

# Convert event_codes into dictionary
event_codes = event_codes.apply(lambda x: x.str.strip() if x.dtype == 'object' else x)
event_codes_dict = {}
for i, event_code in event_codes.iterrows():
	event_type = event_code['Event_Msg_Type']
	if event_type not in event_codes_dict:
		event_codes_dict[event_type] = {}
	event_codes_dict[event_type][event_code['Action_Type']] = {'event_description': event_code['Event_Msg_Type_Description'], 'activity_description': event_code['Action_Type_Description']}
event_codes = event_codes_dict

# Iterate through games
total_ratings = {}
for game in sorted(play_by_play['Game_id'].unique()):
	player_ratings, teams = {}, []

	# Get all players for this game and their statuses
	game_player_info = game_lineup.loc[game_lineup['Game_id'] == game]
	player_statuses = game_player_info.loc[game_player_info['Period'] == 0]
	for _, player_info in player_statuses.iterrows():
		player, player_team, player_active = player_info['Person_id'], player_info['Team_id'], player_info['status'] == 'A'
		player_ratings[player] = {'team': player_team, 'active': player_active, 'raw_off_rtg': 0, 'raw_def_rtg': 0, 'total_off_psns': 0, 'total_def_psns': 0, 'personal_pts': 0}
		if player_team not in teams:
			teams.append(player_team)

	# Iterate over each period
	num_periods = max(game_player_info['Period'].unique())
	for i in range(1, num_periods+1):
		print('Begin period {0}'.format(i))

		# Get and sort set of plays for period
		period_plays = play_by_play.loc[(play_by_play['Game_id'] == game) & (play_by_play['Period'] == i)]
		period_plays = period_plays.sort_values(by=['PC_Time', 'WC_Time', 'Event_Num'], ascending=[False, True, True])

		# Group plays into possessions
		possession_plays, curr_possession_plays, team_with_possession, prev_play, play_counter = [], [], None, None, 0
		for _, curr_play in period_plays.iterrows():
			curr_event, curr_action = curr_play['Event_Msg_Type'], curr_play['Action_Type']

			# Initialize if first play of period
			if team_with_possession is None:
				if curr_event == 10 or (i > 1 and i < 5 and curr_event != 16):
					team_with_possession = teams.index(curr_play['Team_id'])
					curr_possession_plays.append(curr_play)
					prev_play = curr_play
				continue
			prev_event, prev_action = prev_play['Event_Msg_Type'], prev_play['Action_Type']

			# Determine if possession change has occured
			change_possession = False
			# Get the team committing the current action
			try:
				team_committing_curr_action = player_ratings[curr_play['Person1']]['team']
			except:
				if curr_event == 4:
					team_committing_curr_action = period_plays.iloc[play_counter+1]['Team_id']
				elif curr_event == 6 and curr_action != 4:
					team_committing_curr_action = teams[1 - teams.index(curr_play['Team_id'])]
				elif curr_event == 6 and curr_action == 4:
					team_committing_curr_action = curr_play['Team_id']
			# If previous event was a made shot, change possession unless foul committed
			if prev_event == 1:
				if not (curr_event == 6 and team_committing_curr_action != teams[team_with_possession]):
					change_possession = True
				elif curr_event == 6 and curr_action == 4 and team_committing_curr_action != teams[team_with_possession]:
					change_possession = True
			# If previous event was a turnover, change possession
			elif prev_event == 5:
				change_possession = True
			# If previous event was a last made free throw or missed free throw => defensive rebound, change possession
			elif prev_event == 3:
				previous_action_message = event_codes[prev_event][prev_action]['activity_description']
				if previous_action_message in ['Free Throw Technical', 'Free Throw Clear Path']:
					ft_number, ft_total = 1, 1
				else:
					ft_number, ft_total = int(previous_action_message[-6]), int(previous_action_message[-1])
				if ft_number == ft_total:
					if prev_play['Option1'] == 1:
						change_possession = True
					elif curr_event == 4 and team_committing_curr_action != teams[team_with_possession]:
						change_possession = True
				if 'Technical' in previous_action_message or 'Flagrant' in previous_action_message or 'Clear Path' in previous_action_message:
					change_possession = False
			# If previous event was a missed shot followed by a defensive rebound, change possession
			elif prev_event == 2 and curr_event == 4:
				if team_committing_curr_action != teams[team_with_possession]:
					change_possession = True

			# Perform the possession change if needed
			if change_possession:
				possession_plays.append({'team': teams[team_with_possession], 'plays': curr_possession_plays})
				team_with_possession, curr_possession_plays = 1 - team_with_possession, []
			# Assertions to ensure correctness
			if curr_event == 1:
				try:
					assert player_ratings[curr_play['Person1']]['team'] == teams[team_with_possession]
				except:
					team_with_possession = teams.index(player_ratings[curr_play['Person1']]['team'])
			# Update variables
			curr_possession_plays.append(curr_play)
			prev_play, play_counter = curr_play, play_counter + 1

		# Catch last possession if remaining
		if curr_possession_plays:
			possession_plays.append({'team': teams[team_with_possession], 'plays': curr_possession_plays})

		# Initialize set of active players at start of period
		starter_info = game_player_info.loc[game_player_info['Period'] == i]
		active_players = list(starter_info['Person_id'].unique())

		# Aggregate offensive/defensive stats per possession
		for possession in possession_plays:
			offensive_team, substitution_queue = possession['team'], []

			# Increment counters for starting active players
			for player in active_players:
				if player_ratings[player]['team'] == offensive_team:
					player_ratings[player]['total_off_psns'] += 1
				else:
					player_ratings[player]['total_def_psns'] += 1

			# Iterate through plays in possession
			for play in possession['plays']:
				event, action, hold_subs = play['Event_Msg_Type'], play['Action_Type'], False
				# Add to ratings if made basket
				if event == 1:
					points_scored = play['Option1']
					for player in active_players:
						if player_ratings[player]['team'] == offensive_team:
							player_ratings[player]['raw_off_rtg'] += points_scored
						else:
							player_ratings[player]['raw_def_rtg'] += points_scored
					player_ratings[play['Person1']]['personal_pts'] += points_scored
				# Hold off substitutions if shooting foul
				elif event == 6 and action in [2, 9, 11, 14, 15, 17, 25, 29]:
					hold_subs = True
				# Add to ratings if made free throw
				elif event == 3:
					points_scored = int(play['Option1'] == 1)
					for player in active_players:
						if player_ratings[player]['team'] == offensive_team:
							player_ratings[player]['raw_off_rtg'] += points_scored
						else:
							player_ratings[player]['raw_def_rtg'] += points_scored
					player_ratings[play['Person1']]['personal_pts'] += points_scored
					action_message = event_codes[event][action]['activity_description']
					if action_message in ['Free Throw Technical', 'Free Throw Clear Path']:
						ft_number, ft_total = 1, 1
					else:
						ft_number, ft_total = int(action_message[-6]), int(action_message[-1])
					# Deal with substitution queue if last free throw in sequence
					if ft_number == ft_total:
						hold_subs = False
						for substitution in substitution_queue:
							active_players.remove(substitution['leaving_player'])
							active_players.append(substitution['entering_player'])
						substitution_queue = []
				# Handle substitutions
				elif event == 8:
					if hold_subs:
						substitution_queue.append({'leaving_player': play['Person1'], 'entering_player': play['Person2']})
					else:
						active_players.remove(play['Person1'])
						active_players.append(play['Person2'])

			# Assert substitution queue is empty
			assert substitution_queue == []

	# Calculate true player ratings
	for player in player_ratings:
		if player_ratings[player]['total_off_psns'] != 0:
			player_ratings[player]['true_off_rtg'] = player_ratings[player]['raw_off_rtg']/player_ratings[player]['total_off_psns']*100
		if player_ratings[player]['total_def_psns'] != 0:
			player_ratings[player]['true_def_rtg'] = player_ratings[player]['raw_def_rtg']/player_ratings[player]['total_def_psns']*100
	total_ratings[game] = player_ratings

# Save to CSV
submission = pd.DataFrame(columns=['Game_ID', 'Player_ID', 'OffRtg', 'DefRtg'])
for game in total_ratings:
	for player in total_ratings[game]:
		player_off_rtg = total_ratings[game][player].get('true_off_rtg', np.nan)
		player_def_rtg = total_ratings[game][player].get('true_def_rtg', np.nan)
		submission = submission.append({'Game_ID': game, 'Player_ID': player, 'OffRtg': player_off_rtg, 'DefRtg': player_def_rtg}, ignore_index=True)
submission.to_csv('ArvindSridhar_Q1_BBALL.csv')
print('Saved CSV')
