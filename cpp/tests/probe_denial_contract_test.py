import copy
import unittest
from probe_denial_contract import validate_denials


ORIGIN='https://central.example.invalid'
PATH='/download?size=10240&acceptanceProbe=unique-0'
OBS=[dict(method='GET',path=PATH,outcome='fetch-rejected')]
LOG=[dict(status=404,request=dict(method='OPTIONS',uri=PATH,headers={
    'Origin':[ORIGIN],'Sec-Fetch-Site':['cross-site'],'Authorization':['[REDACTED]']}))]


class Tests(unittest.TestCase):
    def test_corroborated_preflight(self):
        result=validate_denials(OBS,LOG,ORIGIN)
        self.assertEqual(result,[dict(method='GET',outcome='preflight-denied',corroborating_records=1)])
        self.assertNotIn('Authorization',str(result))

    def test_direct_response(self):
        observation=copy.deepcopy(OBS);observation[0]['outcome']='response-404'
        logs=copy.deepcopy(LOG);logs[0]['request']['method']='GET'
        self.assertEqual(validate_denials(observation,logs,ORIGIN)[0]['outcome'],'response-denied')

    def test_typeerror_without_http_is_not_proof(self):
        with self.assertRaises(AssertionError):validate_denials(OBS,[],ORIGIN)

    def test_origin_and_status_are_required(self):
        for key,value in [('status',200),('origin','https://other.example.invalid')]:
            logs=copy.deepcopy(LOG)
            if key=='status':logs[0]['status']=value
            else:logs[0]['request']['headers']['Origin']=[value]
            with self.assertRaises(AssertionError):validate_denials(OBS,logs,ORIGIN)

    def test_actual_payload_contradicts_preflight_denial(self):
        logs=copy.deepcopy(LOG);payload=copy.deepcopy(logs[0]);payload['request']['method']='GET';logs.append(payload)
        with self.assertRaises(AssertionError):validate_denials(OBS,logs,ORIGIN)

    def test_nonce_and_uniqueness(self):
        for observations in [OBS+OBS,[dict(method='GET',path='/download?size=10240',outcome='fetch-rejected')]]:
            with self.assertRaises(AssertionError):validate_denials(observations,LOG,ORIGIN)


if __name__=='__main__':unittest.main()
