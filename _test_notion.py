import sys, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, '.')
from dotenv import dotenv_values
cfg = dotenv_values('.env')
token = cfg.get('NOTION_TOKEN', '').strip("'").strip('"')

import app_flet

dbs, err = app_flet.list_notion_databases(token)
print(f'list_notion_databases -> {len(dbs)} dbs | err={err}')
for db in dbs:
    title = app_flet.get_db_title(db)
    db_id = db['id']
    print(f'\n  [{db_id}] {title}')
    props = app_flet.get_database_properties(token, db_id)
    print(f'  Props ({len(props)}):')

    t  = app_flet.props_by_type(props, 'title')
    tx = app_flet.props_by_type(props, 'rich_text')
    dt = app_flet.props_by_type(props, 'date', 'created_time', 'last_edited_time')
    sl = app_flet.props_by_type(props, 'select', 'status')

    print(f'    title fields:   {t}')
    print(f'    content fields: {tx}')
    print(f'    date fields:    {dt}')
    print(f'    select/status:  {sl}')
    print(f'  -> Wizard step 3 populado com {len(t)+len(tx)+len(dt)+len(sl)} campos mapeáveis')
