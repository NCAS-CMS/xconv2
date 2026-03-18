from pathlib import Path
import json
import warnings
from urllib.parse import urlparse

def get_locations(config_file=None):
    """ 
    Read config file (config.json) and find usable locations.
    We are expecting a file in the "minio configuration format",
    and we expect it in either ~/.mc/ or in ~/.config/cfview.
    """

    if config_file is None:
        candidates = [
            Path.home() / '.mc/config.json',
            Path.home() / '.config/cfview/config.json',
        ]
        config_file = next((path for path in candidates if path.is_file()), None)
        if config_file is None:
            return None
    else:
        config_file = Path(config_file).expanduser()
        if not config_file.is_file():
            raise FileNotFoundError(f'Configuration file not found: {config_file}')

    try:
        with open(config_file, 'r', encoding='utf-8') as jfile:
            jdata = json.load(jfile)
        jd = jdata['aliases']
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError(f'Malformed configuration file: {config_file}') from exc

    locations = {}
    servers = {}
    for k, v in jd.items():
        api = v.get('api')
        if (api or '').lower() != 's3v4':
            warnings.warn(f'WARNING: Found unexpected S3 API {api} for {k} in configuration file {config_file}')
        else:
            locations[k] = v

            parsed = urlparse(v.get('url', ''))
            host = parsed.netloc or parsed.path
            if host:
                servers[host] = k

    return locations, servers


def get_user_config(target, config_file=None):
    """
    Obtain credentials from user configuration file
    """
    if config_file is None:
        config_file = Path.home()/'.mc/config.json'
    options = get_locations(config_file)
    if options is None:
        raise ValueError(f'No configuration file found for target [{target}]')

    locations, _ = options
    try:
        return locations[target]
    except KeyError:
        raise ValueError(f'Target [{target}] not found in ~/{config_file}')
