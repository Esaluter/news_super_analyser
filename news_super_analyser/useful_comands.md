# Ручное получение новостей из первой таблицы
# https://docs.google.com/spreadsheets/d/1TTala_o6iV-8tSXvAWM-zsgQqi2JF2uGulyy3XVu7qI/edit?gid=0#gid=0
python -m news_super_analyser once 

# Получение текстов из ссылок
# https://docs.google.com/spreadsheets/d/1t-QAB23gHpggzHEq49AGVfpjHf2cu7jcxRY9fObbOL8/edit?gid=0#gid=0
python -m news_super_analyser.full_text_cli --limit 20 --request-delay 1 --apply


# Запуск анализатора гига чата 
python -m news_super_analyser.ai_analysis_cli --limit 5 --apply

# Отправка и проверка телеграм канала 
python check_telegram.py
python check_telegram.py --send