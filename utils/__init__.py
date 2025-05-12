"""Module with usefull classes and funcs
"""
import requests
from xml.etree import ElementTree as ET
# Function to get feature count using GetFeature with resultType=hits
def get_feature_count(feature_name, host):
    # Build the GetFeature request URL with resultType=hits
    params = {
        'service': 'WFS',
        'version': '2.0.0',
        'request': 'GetFeature',
        'typenames': feature_name,
        'resultType': 'hits'
    }
    response = requests.get(host, params=params)
    
    # Check the response status
    if response.status_code == 200:
        # Extract the total number of features from the XML response
        tree = ET.fromstring(response.content)
        return int(tree.attrib['numberMatched'])  # 'numberMatched' gives the total number of features
    else:
        logging.error(f"Failed to fetch feature count for {feature_name}. HTTP Status Code: {response.status_code}")
        return None
