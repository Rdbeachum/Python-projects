import csv
import json
import re
import sys
import time
from dataclasses import dataclass, asdict, field
from typing import List, Dict, Optional
from urllib.parse import urljoin

pip :install/requests
from bs4 import BeautifulSoup
