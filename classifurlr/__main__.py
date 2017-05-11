import argparse, json, logging
import classifurlr

def parse_args():
    parser = argparse.ArgumentParser(description='Determine whether a collection of pages is inaccessible')
    parser.add_argument('session_file', type=open,
            help='file containing JSON detailing HTTP requests + responses')
    parser.add_argument('--debug', action='store_true',
            help='Log debugging info')
    return parser.parse_args()

if __name__ == '__main__':
    args = parse_args()
    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
    c = classifurlr.run(json.load(args.session_file))
    print(c.as_json())
