# satkas/cli/cli.py
import logging

logging.basicConfig(level=logging.INFO)


def main():
    from satkas.main import main as satkas_main
    satkas_main()


if __name__ == "__main__":
    main()
