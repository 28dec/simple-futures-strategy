up:
	docker compose run --rm freqtrade backtesting --timeframe 1h --export trades --breakdown week -s SimpleFutures -c user_data/config.json -c user_data/exchange-config.json --timerange 20200101- > README.txt && cat README.txt

data:
	docker compose run --rm freqtrade download-data --trading-mode futures -c user_data/exchange-config.json -p BTC/USDT:USDT ETH/USDT:USDT BNB/USDT:USDT --timerange 20200101- --timeframe 1h

hyperopt:
	docker compose run --rm freqtrade hyperopt -c user_data/config.json -c user_data/exchange-config.json -j 3 --timerange 20200101- --timeframe 1h --hyperopt-loss SortinoHyperOptLossDaily -s SimpleFutures -e 1000

edit:
	sudo -E vim user_data/strategies/SimpleFutures.py
