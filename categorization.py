import json, logging, urllib.parse, urllib.request, csv

class Categorization:
    CORE_ENDPOINT = 'https://core.thenetmonitor.org/'
    CATEGORY_CSV = 'categories.csv'
    CATEGORY_CSV_URL = 'https://raw.githubusercontent.com/berkmancenter/url-lists/master/category_codes.csv'
    @classmethod
    def get_url_category(cls, url, country=None):
        # Rather not make this a network call, but the logic is pretty
        # complicated given multi-country lists (like global).
        endpoint = cls.CORE_ENDPOINT + 'urls/categorize'
        data = { 'url': url }
        if country is not None:
            data['country'] = country
        encoded = urllib.parse.urlencode(data).encode('ascii')
        with urllib.request.urlopen(endpoint, encoded) as f:
            try:
                categories = json.loads(f.read().decode('utf-8'))
                if country is None:
                    return categories
                return categories[country]
            except KeyError as e:
                logging.warning('Failed to find category - URL: "{}", Country: '
                        '"{}"'.format(url, country))
                return None

    @classmethod
    def get_urls_categories(cls, urls, country=None):
        endpoint = cls.CORE_ENDPOINT + 'urls/categorize'
        data = { 'url[]': urls }
        if country is not None:
            data['country'] = country
        encoded = urllib.parse.urlencode(data, doseq=True).encode('ascii')
        with urllib.request.urlopen(endpoint, encoded) as f:
            try:
                categories = json.loads(f.read().decode('utf-8'))
                if country is None:
                    return categories
                return { url: c[country] for url, c in categories.items()}
            except KeyError as e:
                logging.warning('Failed to find category - URL: "{}", Country: '
                        '"{}"'.format(urls, country))
                return None

    @classmethod
    def get_theme_categories(cls, theme):
        categories = []
        csv_file = cls._get_categories_csv()
        for row in csv.DictReader(csv_file):
            if row['theme'].strip().upper() == theme.upper():
                categories.append(row['code'].strip().upper())
        csv_file.close()
        return categories

    @classmethod
    def get_category_theme(cls, category):
        csv_file = cls._get_categories_csv()
        for row in csv.DictReader(csv_file):
            if row['code'].strip().upper() == category.strip().upper():
                csv_file.close()
                return row['theme'].strip()
        csv_file.close()
        return None

    @classmethod
    def _dl_categories_file(cls):
        logging.info('Downloading categories file')
        with urllib.request.urlopen(cls.CATEGORY_CSV_URL) as u, open(cls.CATEGORY_CSV, 'w') as f:
            data = u.read().decode('utf-8')
            f.write(data)
    
    @classmethod
    def _get_categories_csv(cls):
        try:
            category_csv_file = open(cls.CATEGORY_CSV, 'r')
        except FileNotFoundError as e:
            cls._dl_categories_file()
            category_csv_file = open(cls.CATEGORY_CSV, 'r')
        return category_csv_file
