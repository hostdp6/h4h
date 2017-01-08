from django.shortcuts import render
from django.db.models import Q
from django.core.exceptions import ObjectDoesNotExist
from django.http import JsonResponse
from django.conf import settings

from rest_framework import permissions, viewsets, exceptions, status
from rest_framework.views import APIView
from rest_framework.renderers import JSONRenderer
from rest_framework.pagination import PageNumberPagination
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.response import Response
from rest_framework.renderers import JSONRenderer
from rest_framework_bulk import BulkModelViewSet
from rest_framework.exceptions import ValidationError, ParseError
from django.http import Http404
from rest_framework import status
from mvam.serializers import *
from rest_framework.views import APIView

from . import serializers, models, scorer, storer


class DeviceTypeViewSet(viewsets.ModelViewSet):
    serializer_class = DeviceTypeSerializer
    queryset = models.DeviceType.objects.all()


class HandleResponse(APIView):

    def is_new_survey(self, request):

        try:
            _ = request.data['question']['question_id']
            return False
        except KeyError:
            return True

    def is_valid_request(self, request):

        try:
            _ = request.data['respondent']['respondent_id']
            _ = request.data['timestamp']
            _ = request.data['session_id']
            return True
        except KeyError:
            return False

    def get_respondent(self, request):

        _respondent = request.data['respondent']

        try:
            respondent = models.Respondent.objects.get(
                respondent_id=_respondent['respondent_id']
            )

            if 'location' in _respondent.keys() and 'location_type' in _respondent.keys():
                new_location = self.get_location(_respondent)
                respondent.location = new_location['location']
                respondent.location_type = new_location['location_type']
                respondent.save()

        except ObjectDoesNotExist:
            location = self.get_location(_respondent)
            respondent = models.Respondent(
                respondent_id=_respondent['respondent_id'],
                location=location['location'],
                location_type=location['location_type']
            )
            respondent.save()

        return respondent

    def get_location(self, data):

        try:
            request_location = data['location']
            request_location_type = data['location_type']
        except KeyError:
            raise ParseError('location and location type are required')

        location = models.Locations.objects.get_or_create(
            location=request_location
        )

        location_type = models.LocationType.objects.get_or_create(
            location_type=request_location_type
        )

        return {
            'location': location[0],
            'location_type': location_type[0]
        }

    def get_survey_question(self, request):

        try:
            survey_question = models.SurveyQuestion.objects.get(
                id=request.data['question']['question_id']
            )
        except KeyError:
            return None
        except ObjectDoesNotExist:
            raise ParseError('the question ID provided is invalid')

    def parse_request(self, request):

        parsed_request = request.data
        parsed_request['respondent'] = self.get_respondent(request)
        try:
            parsed_request['question']['question_id'] = self.get_survey_question(request)
        except KeyError:
            pass

        return parsed_request

    def got_a_response(self, data):

        try:
            _ = data['raw_response']
            _ = data['question']['question_id']
            _ = data['question']['metrics']
            return True
        except KeyError:
            return False

    def get_next_survey(self, respondent):

        print respondent
        print respondent.location

        NO_SURVEY = {
            'on_next': 'TERMINATE',
            'message': 'Sorry, there are no active surveys in your area'
        }

        try:
            location = models.Locations.objects.get(
                location=respondent.location
            )
        except ObjectDoesNotExist:
            return NO_SURVEY

        try:
            return models.Survey.objects.filter(
                geo_scope=location
            )[0]
        except IndexError:
            return NO_SURVEY

    def get_language(self, request):

        try:
            return request['respondent'].language.language
        except AttributeError:
            return None

    def get_device_type(self, request):

        try:
            return request['respondent'].device_type.device_type
        except AttributeError:
            return None

    def get_language_type(self, request):

        try:
            return request['respondent'].language_type.language_type
        except AttributeError:
            return None

    def get_termination(self, termination, request):

        language = self.get_language(request)
        language_type = self.get_language_type(request)
        device_type = self.get_device_type(request)

        response = request
        response['respondent'] = {
            'respondent_id': response['respondent'].respondent_id,
            'location': response['respondent'].location.location,
            'location_type': language_type,
            'language': language,
            'device_type': device_type,
        }
        response['raw_response'] = None
        response['question'] = {
            'question_text': termination['message']
        }
        response['on_next'] = termination['on_next']

        return response

    def get_question_metrics(self, question):

        metrics = models.QuestionMetric.objects.filter(
            question=question
        )

        formatted_metrics = []
        for metric in metrics:
            formatted_metrics.append({
                'metric_id': metric.id,
                'metric_type': metric.type
            })

        return formatted_metrics

    def get_first_question(self, survey, request):

        language = self.get_language(request.data)
        language_type = self.get_language_type(request.data)
        device_type = self.get_device_type(request.data)

        response = request.data
        response['respondent'] = {
            'respondent_id': response['respondent'].respondent_id,
            'location': response['respondent'].location.location,
            'location_type': language_type,
            'language': language,
            'device_type': device_type,
        }
        response['raw_response'] = None
        response['question'] = {
            'question_id': survey.first_question.id,
            'question_text': survey.first_question.text,
            'metrics': self.get_question_metrics(survey.first_question)
        }

        return response

    def post(self, request):

        if not self.is_valid_request(request):
            raise ParseError(
                'invalid request body'
            )
        parsed_request = self.parse_request(request)

        # is this a new survey
        if self.is_new_survey(request):

            # determine which survey to return
            survey = self.get_next_survey(parsed_request['respondent'])
            if type(survey) is not models.Survey:
                return Response(self.get_termination(survey, parsed_request))

            return Response(self.get_first_question(
                survey,
                request
            ))

        # if it isn't a new question, but we didn't get any response
        # respond with the same question
        if not self.got_a_response(request.data):
            return Response(request.data)

        # now we know that we actually need to process the response
        # first store the response metrics
        storer.store_response(parsed_request)

        # then we need to score the response
        next_question = scorer.score_response(parsed_request)

        # format a new response based on the next question

        return Response({'STATUS': 'NEXT QUESTION'})


class LabelViewset(viewsets.ModelViewSet):
    serializer_class = LabelSerializer
    queryset = models.Label.objects.all()


class LanguageViewset(viewsets.ModelViewSet):
    serializer_class = LanguageSerializer
    #permission_classes = [IsAccountAdminOrReadOnly]
    queryset = models.Language.objects.all()


class LocationsViewset(viewsets.ModelViewSet):
    serializer_class = LocationsSerializer
    #permission_classes = [IsAccountAdminOrReadOnly]
    queryset = models.Locations.objects.all()


class LocationTypeViewset(viewsets.ModelViewSet):
    serializer_class = LocationTypeSerializer
    #permission_classes = [IsAccountAdminOrReadOnly]
    queryset = models.LocationType.objects.all()


class MetricViewSet(viewsets.ModelViewSet):
    serializer_class = MetricSerializer
    queryset = models.Metric.objects.all()


class MetricResponseViewset(viewsets.ModelViewSet):
    serializer_class = MetricResponseSerializer

    def get_queryset(self):

        queryset = models.MetricResponse.objects.all()

        question = self.request.query_params.get(
            'question',
            None
        )
        if question is not None:
            queryset = queryset.filter(
                response__question__id=question
            )

        metric = self.request.query_params.get(
            'metric',
            None
        )
        if metric is not None:
            queryset = queryset.filter(
                metric__id=metric
            )

        start = self.request.query_params.get(
            'start',
            None
        )
        if start is not None:
            queryset = queryset.filter(
                timestamp__gte=start
            )

        end = self.request.query_params.get(
            'end',
            None
        )
        if end is not None:
            queryset = queryset.filter(
                timestamp__lte=end
            )

        return queryset


class MetricTypeViewset(viewsets.ModelViewSet):
    serializer_class = MetricTypeSerializer
    queryset = models.MetricType.objects.all()


class OccupationViewset(viewsets.ModelViewSet):
    serializer_class = OccupationSerializer
    #permission_classes = [IsAccountAdminOrReadOnly]
    queryset = models.Occupation.objects.all()


class QuestionViewSet(viewsets.ModelViewSet):
    serializer_class = QuestionSerializer
    queryset = models.Question.objects.all()


class QuestionMetricViewset(viewsets.ModelViewSet):
    serializer_class = QuestionMetricSerializer

    def get_queryset(self):

        queryset = models.QuestionMetric.objects.all()

        question = self.request.query_params.get(
            'question',
            None
        )
        if question is not None:
            queryset = queryset.filter(
                question__id=question
            )

        return queryset


class QuestionLabelViewset(viewsets.ModelViewSet):
    serializer_class = QuestionLabelSerializer

    def get_queryset(self):

        queryset = models.QuestionLabel.objects.all()

        label = self.request.query_params.get(
            'label',
            None
        )
        if label is not None:
            queryset = queryset.filter(
                label__label=label
            )

        question = self.request.query_params.get(
            'question',
            None
        )
        if question is not None:
            queryset = queryset.filter(
                question__id=question
            )

        return queryset


class RespondentViewset(viewsets.ModelViewSet):
    serializer_class = RespondentSerializer
    queryset = models.Respondent.objects.all()


class ResponseViewset(viewsets.ModelViewSet):
    serializer_class = ResponseSerializer
    #permission_classes = [IsAccountAdminOrReadOnly]
    queryset = models.Response.objects.all()


class SurveyViewset(viewsets.ModelViewSet):
    serializer_class = SurveySerializer
    queryset = models.Survey.objects.all()


class SurveyQuestionViewSet(viewsets.ModelViewSet):
    serializer_class = SurveyQuestionSerializer
    queryset = models.SurveyQuestion.objects.all()


class SurveyQuestionRuleViewSet(viewsets.ModelViewSet):
    serializer_class = SurveyQuestionRuleSerializer
    queryset = models.SurveyQuestionRule.objects.all()


class SurveyQuestionRuleArgumentViewSet(viewsets.ModelViewSet):
    serializer_class = SurveyQuestionRulesArgumentSerializer
    queryset = models.SurveyQuestionRulesArgument.objects.all()


class SurveyTypeViewset(viewsets.ModelViewSet):
    serializer_class = SurveyTypeSerializer
    #permission_classes = [IsAccountAdminOrReadOnly]
    queryset = models.SurveyType.objects.all()
