from django.db import connection
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.http import HttpResponseRedirect
from urlparse import urlparse
import re
from .models import Indicator, PeriodicTarget, DisaggregationLabel, DisaggregationValue, CollectedData, IndicatorType, Level, ExternalServiceRecord, ExternalService, TolaTable
from workflow.models import Program, SiteProfile, Country, Sector, TolaSites, TolaUser, FormGuidance
from django.shortcuts import render_to_response
from django.contrib import messages
from django.core.serializers.json import DjangoJSONEncoder
from tola.util import getCountry, get_table
from tables import IndicatorDataTable
from django_tables2 import RequestConfig
from workflow.forms import FilterForm
from .forms import IndicatorForm, CollectedDataForm

from django.db.models import Count, Sum, Max, Min
from django.db.models import Q
from django.views.generic.edit import CreateView, UpdateView, DeleteView
from django.views.generic.list import ListView
from django.views.generic.detail import View
from django.views.generic import TemplateView
from django.utils.decorators import method_decorator
from django.contrib.auth.decorators import user_passes_test
from django.core.exceptions import PermissionDenied
from django.core import serializers
from django.utils import timezone
from django.urls import reverse, reverse_lazy

from workflow.mixins import AjaxableResponseMixin
import json

import requests
from export import IndicatorResource, CollectedDataResource
# from reportlab.pdfgen import canvas
from weasyprint import HTML, CSS
from django.template.loader import get_template
from django.http import HttpResponse
from datetime import datetime
from dateutil.relativedelta import relativedelta #('%Y-%m-%d') %b %d, %Y
from feed.serializers import FlatJsonSerializer
import dateutil.parser


def generate_periodic_target_single(tf, start_date, nthTargetPeriod, target_frequency_custom=''):
    i = nthTargetPeriod
    j = i + 1
    target_period = ''

    if tf == Indicator.LOP:
        lop_target = Indicator.TARGET_FREQUENCIES[Indicator.LOP-1][1]
        return {'period': lop_target }
    elif tf == Indicator.MID_END:
        return [ {'period': 'Midline'}, {'period': 'Endline'} ]
    elif tf == Indicator.EVENT:
        return { 'period': target_frequency_custom }


    if tf == Indicator.ANNUAL:
        start = ( (start_date + relativedelta(years =+ i) ).replace(day=1) ).strftime('%Y-%m-%d')
        end = ( (start_date + relativedelta(years =+ j) ) + relativedelta(days =- 1) ).strftime('%Y-%m-%d')
        target_period = {'period': 'Year %s' % j, 'start_date': start, 'end_date': end }
    elif tf == Indicator.SEMI_ANNUAL:
        start = ( ( start_date + relativedelta(months =+ (i*6)) ).replace(day=1) ).strftime('%Y-%m-%d')
        end = ( (start_date + relativedelta(months =+ (j*6)) ) + relativedelta(days =- 1) ).strftime('%Y-%m-%d')
        target_period = {'period': 'Semi-annual period %s' % j, 'start_date': start, 'end_date': end }
    elif tf == Indicator.TRI_ANNUAL:
        start = ( ( start_date + relativedelta(months =+ (i*4))).replace(day=1) ).strftime('%Y-%m-%d')
        end = ( (start_date + relativedelta(months =+ (j*4))) + relativedelta(days=-1) ).strftime('%Y-%m-%d')
        target_period = {'period': 'Tri-annual period %s' % j, 'start_date': start, 'end_date': end }

    elif tf == Indicator.QUARTERLY:
        start = ( (start_date + relativedelta(months =+ (i*3)) ).replace(day=1)).strftime('%Y-%m-%d')
        end = ( ( start_date + relativedelta(months =+ (j*3))) + relativedelta(days=-1) ).strftime('%Y-%m-%d')
        target_period = {'period': 'Quarter %s' % j, 'start_date': start, 'end_date': end }
    elif tf == Indicator.MONTHLY:
        month = ( start_date + relativedelta(months =+ i) ).strftime("%B")
        year = ( start_date + relativedelta(months =+ i )).strftime("%Y")
        name = month + " " + year
        start = (( start_date + relativedelta(months =+ i)).replace(day=1)).strftime('%Y-%m-%d')
        #end =  ( ( start_date + relativedelta(months =+ j)) + relativedelta(day=1, months=+1, days=-1)).strftime('%Y-%m-%d')
        end = ( ( start_date + relativedelta(months =+ j)) + relativedelta(days=-1) ).strftime('%Y-%m-%d')
        target_period = {'period': name, 'start_date': start, 'end_date': end }
    return target_period


def generate_periodic_targets(tf, start_date, numTargets, target_frequency_custom=''):
    gentargets = []
    target_period = None

    if tf == Indicator.LOP or tf == Indicator.MID_END:
        target_period = generate_periodic_target_single(tf, start_date, numTargets)
        return target_period

    for i in range(numTargets):
        j = i + 1
        target_period = generate_periodic_target_single(tf, start_date, i, target_frequency_custom)
        gentargets.append(target_period)
    return gentargets


def group_excluded(*group_names, **url):
    """
    If user is in the group passed in permission denied
    :param group_names:
    :param url:
    :return: Bool True or False is users passes test
    """
    def in_groups(u):
        if u.is_authenticated():
            if not bool(u.groups.filter(name__in=group_names)):
                return True
            raise PermissionDenied
        return False
    return user_passes_test(in_groups)


class IndicatorList(ListView):
    """
    Main Indicator Home Page, displays a list of Indicators Filterable by Program
    """
    model = Indicator
    template_name = 'indicators/indicator_list.html'

    def get(self, request, *args, **kwargs):

        # countries = getCountry(request.user)
        countries = request.user.tola_user.countries.all()
        getPrograms = Program.objects.filter(funding_status="Funded", country__in=countries).distinct()
        getIndicators = Indicator.objects.filter(program__country__in=countries)#.exclude(collecteddata__isnull=True)
        getIndicatorTypes = IndicatorType.objects.all()

        program_id = int(self.kwargs['program'])
        indicator_id = int(self.kwargs['indicator'])
        type_id = int(self.kwargs['type'])

        filters = {'id__isnull': False}
        if program_id != 0:
            filters['id'] = program_id
            getIndicators = getIndicators.filter(program__in=[program_id])

        if type_id != 0:
            filters['indicator__indicator_type__id'] = type_id
            getIndicators = getIndicators.filter(indicator_type=type_id)

        if indicator_id != 0:
            filters['indicator'] = indicator_id

        programs = Program.objects.prefetch_related('indicator_set')\
            .filter(funding_status="Funded", country__in=countries)\
            .filter(**filters).order_by('name')\
            .annotate(indicator_count=Count('indicator'))

        return render(request, self.template_name, {
                        'getPrograms': getPrograms,
                        'getIndicators':getIndicators,
                        'getIndicatorTypes': getIndicatorTypes,
                        'program_id':program_id,
                        'indicator_id': indicator_id,
                        'type_id': type_id,
                        'programs': programs})


def import_indicator(service=1,deserialize=True):
    """
    Import a indicators from a web service (the dig only for now)
    :param service:
    :param deserialize:
    :return:
    """
    service = ExternalService.objects.get(id=service)
    response = requests.get(service.feed_url)

    if deserialize == True:
        data = json.loads(response.content) # deserialises it
    else:
        # send json data back not deserialized data
        data = response
    #debug the json data string uncomment dump and print
    #data2 = json.dumps(json_data) # json formatted string
    #print data2

    return data


def indicator_create(request, id=0):
    """
    Create an Indicator with a service template first, or custom.  Step one in Inidcator creation.
    Passed on to IndicatorCreate to do the creation
    :param request:
    :param id:
    :return:
    """
    getIndicatorTypes = IndicatorType.objects.all()
    getCountries = Country.objects.all()
    countries = getCountry(request.user)
    country_id = Country.objects.get(country=countries[0]).id
    getPrograms = Program.objects.all().filter(funding_status="Funded",country__in=countries).distinct()
    getServices = ExternalService.objects.all()
    program_id = id

    if request.method == 'POST':
        #set vars from form and get values from user

        type = IndicatorType.objects.get(indicator_type="custom")
        country = Country.objects.get(id=request.POST['country'])
        program = Program.objects.get(id=request.POST['program'])
        service = request.POST['services']
        level = Level.objects.all()[0]
        node_id = request.POST['service_indicator']
        sector = None
        # add a temp name for custom indicators
        name = "Temporary"
        source = None
        definition = None
        external_service_record = None

        #import recursive library for substitution
        import re

        #checkfor service indicator and update based on values
        if node_id != None and int(node_id) != 0:
            getImportedIndicators = import_indicator(service)
            for item in getImportedIndicators:
                if item['nid'] == node_id:
                    getSector, created = Sector.objects.get_or_create(sector=item['sector'])
                    sector=getSector
                    getLevel, created = Level.objects.get_or_create(name=item['level'].title())
                    level=getLevel
                    name=item['title']
                    source=item['source']
                    definition=item['definition']
                    #replace HTML tags if they are in the string
                    definition = re.sub("<.*?>", "", definition)

                    getService = ExternalService.objects.get(id=service)
                    full_url = getService.url + "/" + item['nid']
                    external_service_record = ExternalServiceRecord(record_id=item['nid'],external_service=getService,full_url=full_url)
                    external_service_record.save()
                    getType, created = IndicatorType.objects.get_or_create(indicator_type=item['type'].title())
                    type=getType

        #save form
        new_indicator = Indicator(sector=sector,name=name,source=source,definition=definition, external_service_record=external_service_record)
        new_indicator.save()
        new_indicator.program.add(program)
        new_indicator.indicator_type.add(type)
        new_indicator.level.add(level)

        latest = new_indicator.id

        #redirect to update page
        messages.success(request, 'Success, Basic Indicator Created!')
        redirect_url = '/indicators/indicator_update/' + str(latest)+ '/'
        return HttpResponseRedirect(redirect_url)

    # send the keys and vars from the json data to the template along with submitted feed info and silos for new form
    return render(request, "indicators/indicator_create.html", {'country_id': country_id, 'program_id':int(program_id),'getCountries':getCountries,
                                                                'getPrograms': getPrograms,'getIndicatorTypes':getIndicatorTypes, 'getServices': getServices})


class IndicatorCreate(CreateView):
    """
    Indicator Form for indicators not using a template or service indicator first as well as the post reciever
    for creating an indicator.  Then redirect back to edit view in IndicatorUpdate.
    """
    model = Indicator
    template_name = 'indicators/indicator_form.html'

    #pre-populate parts of the form
    def get_initial(self):
        user_profile = TolaUser.objects.get(user=self.request.user)
        initial = {
            'program': self.kwargs['id'],
            }

        return initial

    def get_context_data(self, **kwargs):
        context = super(IndicatorCreate, self).get_context_data(**kwargs)
        context.update({'id': self.kwargs['id']})
        return context

    @method_decorator(group_excluded('ViewOnly', url='workflow/permission'))
    def dispatch(self, request, *args, **kwargs):
        return super(IndicatorCreate, self).dispatch(request, *args, **kwargs)

    # add the request to the kwargs
    def get_form_kwargs(self):
        kwargs = super(IndicatorCreate, self).get_form_kwargs()
        kwargs['request'] = self.request
        program = Indicator.objects.all().filter(id=self.kwargs['pk']).values("program__id")
        kwargs['program'] = program
        return kwargs

    def form_invalid(self, form):

        messages.error(self.request, 'Invalid Form', fail_silently=False)

        return self.render_to_response(self.get_context_data(form=form))

    def form_valid(self, form):
        form.save()
        messages.success(self.request, 'Success, Indicator Created!')
        form = ""
        return self.render_to_response(self.get_context_data(form=form))

    form_class = IndicatorForm


class PeriodicTargetView(View):
    """
    This view is responsible for generating periodic targets or deleting them (via POST)
    """
    model = PeriodicTarget

    def get(self, request, *args, **kwargs):
        indicator = Indicator.objects.get(pk=self.kwargs.get('indicator', None))
        if request.GET.get('existingTargetsOnly'):
            pts = FlatJsonSerializer().serialize(indicator.periodictarget_set.all().order_by('customsort','create_date', 'period'))
            return HttpResponse(pts)
        try:
            numTargets = int(request.GET.get('numTargets', None))
        except Exception as e:
            numTargets = PeriodicTarget.objects.filter(indicator=indicator).count() + 1

        pt_generated = generate_periodic_target_single(indicator.target_frequency, indicator.target_frequency_start, (numTargets-1), '')
        pt_generated_json = json.dumps(pt_generated, cls=DjangoJSONEncoder)
        return HttpResponse(pt_generated_json)

    def post(self, request, *args, **kwargs):
        indicator = Indicator.objects.get(pk=self.kwargs.get('indicator', None))
        deleteall = self.kwargs.get('deleteall', None)
        if deleteall == 'true':
            periodic_targets = PeriodicTarget.objects.filter(indicator=indicator)
            for pt in periodic_targets:
                pt.collecteddata_set.all().update(periodic_target=None)
                pt.delete()
            indicator.target_frequency = None
            indicator.target_frequency_num_periods = 1
            indicator.target_frequency_start = None
            indicator.target_frequency_custom = None
            indicator.save()
        return HttpResponse('{"status": "success", "message": "Request processed successfully!"}')


def handleDataCollectedRecords(indicatr, lop, existing_target_frequency, new_target_frequency, generated_pt_ids=[]):
    # If the target_frequency is changed from LOP to something else then disassociate all
    # collected_data from the LOP periodic_target and then delete the LOP periodic_target
    # if existing_target_frequency == Indicator.LOP and new_target_frequency != Indicator.LOP:
    if existing_target_frequency != new_target_frequency:
        CollectedData.objects.filter(indicator=indicatr).update(periodic_target=None)
        PeriodicTarget.objects.filter(indicator=indicatr).delete()

    # If the user sets target_frequency to LOP then create a LOP periodic_target and associate all
    # collected data for this indicator with this single LOP periodic_target
    if existing_target_frequency != Indicator.LOP and new_target_frequency == Indicator.LOP:
        lop_pt = PeriodicTarget.objects.create(indicator=indicatr, period=Indicator.TARGET_FREQUENCIES[0][1], target=lop, create_date = timezone.now())
        CollectedData.objects.filter(indicator=indicatr).update(periodic_target=lop_pt)

    if generated_pt_ids:
        pts = PeriodicTarget.objects.filter(indicator=indicatr, pk__in=generated_pt_ids)
        for pt in pts:
            CollectedData.objects.filter(indicator=indicatr, date_collected__range=[pt.start_date, pt.end_date]).update(periodic_target=pt)

class IndicatorUpdate(UpdateView):
    """
    Update and Edit Indicators.
    """
    model = Indicator

    #template_name = 'indicators/indicator_form.html'
    def get_template_names(self):
        if self.request.GET.get('modal'):
            return 'indicators/indicator_form_modal.html'
        return 'indicators/indicator_form.html'

    @method_decorator(group_excluded('ViewOnly', url='workflow/permission'))
    def dispatch(self, request, *args, **kwargs):

        if request.method == 'GET':
            # If target_frequency is set but not targets are saved then unset target_frequency too.
            indicator = self.get_object()
            if indicator.target_frequency and \
                    indicator.target_frequency != 1 and \
                    not indicator.periodictarget_set.count():
                indicator.target_frequency = None
                indicator.target_frequency_start = None
                indicator.target_frequency_num_periods = 1
                indicator.save()

        try:
            self.guidance = FormGuidance.objects.get(form="Indicator")
        except FormGuidance.DoesNotExist:
            self.guidance = None
        return super(IndicatorUpdate, self).dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super(IndicatorUpdate, self).get_context_data(**kwargs)
        context.update({'id': self.kwargs['pk']})
        getIndicator = Indicator.objects.get(id=self.kwargs['pk'])

        context.update({'i_name': getIndicator.name})
        context['programId'] = getIndicator.program.all()[0].id
        context['periodic_targets'] = PeriodicTarget.objects.filter(indicator=getIndicator).annotate(num_data=Count('collecteddata')).order_by('customsort','create_date', 'period')
        context['targets_sum'] = PeriodicTarget.objects.filter(indicator=getIndicator).aggregate(Sum('target'))['target__sum']

        #get external service data if any
        try:
            getExternalServiceRecord = ExternalServiceRecord.objects.all().filter(indicator__id=self.kwargs['pk'])
        except ExternalServiceRecord.DoesNotExist:
            getExternalServiceRecord = None
        context.update({'getExternalServiceRecord': getExternalServiceRecord})
        if self.request.GET.get('targetsonly') == 'true':
            context['targetsonly'] = True
        elif self.request.GET.get('targetsactive') == 'true':
            context['targetsactive'] = True

        return context

    def get_initial(self):
        target_frequency_num_periods = self.get_object().target_frequency_num_periods
        if not target_frequency_num_periods:
            target_frequency_num_periods = 1
        initial = {
            'target_frequency_num_periods': target_frequency_num_periods
        }

        return initial

    # add the request to the kwargs
    def get_form_kwargs(self):
        kwargs = super(IndicatorUpdate, self).get_form_kwargs()
        kwargs['request'] = self.request
        program = Indicator.objects.all().filter(id=self.kwargs['pk']).values_list("program__id", flat=True)
        kwargs['program'] = program
        return kwargs

    def form_invalid(self, form):
        messages.error(self.request, 'Invalid Form', fail_silently=False)
        print(".............................%s............................" % form.errors )
        return self.render_to_response(self.get_context_data(form=form))


    def form_valid(self, form, **kwargs):
        periodic_targets = self.request.POST.get('periodic_targets', None)
        indicatr = Indicator.objects.get(pk=self.kwargs.get('pk'))
        generatedTargets = []
        existing_target_frequency = indicatr.target_frequency
        new_target_frequency = form.cleaned_data.get('target_frequency', None)
        lop = form.cleaned_data.get('lop_target', None)

        if periodic_targets == 'generateTargets':
            # handle (delete) association of colelcted data records if necessary
            handleDataCollectedRecords(indicatr, lop, existing_target_frequency, new_target_frequency)

            target_frequency_num_periods = form.cleaned_data.get('target_frequency_num_periods', 0)
            if target_frequency_num_periods == None:
                target_frequency_num_periods = 1

            event_name = form.cleaned_data.get('target_frequency_custom', '')
            start_date = form.cleaned_data.get('target_frequency_start', None)
            generatedTargets = generate_periodic_targets(new_target_frequency, start_date, target_frequency_num_periods, event_name)

        if periodic_targets and periodic_targets != 'generateTargets':
            # now create/update periodic targets
            pt_json = json.loads(periodic_targets)
            generated_pt_ids = []
            for i, pt in enumerate(pt_json):
                pk = int(pt.get('id'))
                if pk == 0: pk = None
                try:
                    start_date = dateutil.parser.parse(pt.get('start_date', None))
                    start_date = datetime.strftime(start_date, '%Y-%m-%d')
                except ValueError:
                    # raise ValueError("Incorrect data value")
                    start_date = None

                try:
                    end_date = dateutil.parser.parse(pt.get('end_date', None))
                    end_date = datetime.strftime(end_date, '%Y-%m-%d')
                except ValueError:
                    #raise ValueError("Incorrect data value")
                    end_date = None

                # print("i = %s.......................%s............................" % (i, periodic_targets) )
                periodic_target,created = PeriodicTarget.objects.update_or_create(\
                    indicator=indicatr, id=pk,\
                    defaults={'period': pt.get('period', ''), 'target': pt.get('target', 0), 'customsort': i,\
                            'start_date': start_date, 'end_date': end_date, 'edit_date': timezone.now() })
                # print("%s|%s = %s, %s" % (created, pk, pt.get('period'), pt.get('target') ))
                if created:
                    periodic_target.create_date = timezone.now()
                    periodic_target.save()
                    generated_pt_ids.append(periodic_target.id)

            # handle related collected_data records for the new periodic targets
            handleDataCollectedRecords(indicatr, lop, existing_target_frequency, new_target_frequency, generated_pt_ids)

        fields_to_watch = set(['indicator_type', 'level', 'name', 'number', 'sector'])
        changed_fields = set(form.changed_data)
        if fields_to_watch.intersection(changed_fields):
            update_indicator_row = '1'
        else:
            update_indicator_row = '1'

        self.object = form.save()
        #periodic_targets = PeriodicTarget.objects.filter(indicator=indicatr).order_by('customsort','create_date', 'period')
        periodic_targets = PeriodicTarget.objects.filter(indicator=indicatr).annotate(num_data=Count('collecteddata')).order_by('customsort','create_date', 'period')

        if self.request.is_ajax():
            data = serializers.serialize('json', [self.object])
            pts = FlatJsonSerializer().serialize(periodic_targets)
            if generatedTargets:
                generatedTargets = json.dumps(generatedTargets, cls=DjangoJSONEncoder)
            else:
                generatedTargets = "[]"

            targets_sum = self.get_context_data().get('targets_sum')
            if targets_sum == None: targets_sum = "0"
            return HttpResponse("[" + data + "," + pts + "," + generatedTargets + "," + str(targets_sum) +  "," + str(update_indicator_row) + "]")
        else:
            messages.success(self.request, 'Success, Indicator Updated!')
        return self.render_to_response(self.get_context_data(form=form))
    form_class = IndicatorForm


class IndicatorDelete(DeleteView):
    """
    Delete and Indicator
    """
    model = Indicator
    success_url = '/indicators/home/0/0/0/'

    @method_decorator(group_excluded('ViewOnly', url='workflow/permission'))
    def dispatch(self, request, *args, **kwargs):
        return super(IndicatorDelete, self).dispatch(request, *args, **kwargs)

    def form_invalid(self, form):

        messages.error(self.request, 'Invalid Form', fail_silently=False)

        return self.render_to_response(self.get_context_data(form=form))

    def form_valid(self, form):

        form.save()

        messages.success(self.request, 'Success, Indicator Deleted!')
        return self.render_to_response(self.get_context_data(form=form))

    form_class = IndicatorForm


class PeriodicTargetDeleteView(DeleteView):
    model = PeriodicTarget

    def delete(self, request, *args, **kwargs):
        collecteddata_count = self.get_object().collecteddata_set.count()
        if collecteddata_count > 0:
            self.get_object().collecteddata_set.all().update(periodic_target=None)

        #super(PeriodicTargetDeleteView).delete(request, args, kwargs)
        indicator = self.get_object().indicator
        self.get_object().delete()
        if indicator.periodictarget_set.count() == 0:
            indicator.target_frequency = None
            indicator.target_frequency_num_periods = 1
            indicator.target_frequency_start = None
            indicator.target_frequency_custom = None
            indicator.save()
        targets_sum = PeriodicTarget.objects.filter(indicator=indicator).aggregate(Sum('target'))['target__sum']
        indicator = None
        return JsonResponse({"status": "success", "msg": "Periodic Target deleted successfully.", "targets_sum": targets_sum})

class CollectedDataCreate(CreateView):
    """
    CollectedData Form
    """
    model = CollectedData
    #template_name = 'indicators/collecteddata_form.html'
    def get_template_names(self):
        if self.request.is_ajax():
            return 'indicators/collecteddata_form_modal.html'
        return 'indicators/collecteddata_form.html'

    form_class = CollectedDataForm

    @method_decorator(group_excluded('ViewOnly', url='workflow/permission'))
    def dispatch(self, request, *args, **kwargs):
        try:
            self.guidance = FormGuidance.objects.get(form="CollectedData")
        except FormGuidance.DoesNotExist:
            self.guidance = None
        return super(CollectedDataCreate, self).dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super(CollectedDataCreate, self).get_context_data(**kwargs)
        try:
            getDisaggregationLabel = DisaggregationLabel.objects.all().filter(disaggregation_type__indicator__id=self.kwargs['indicator'])
            getDisaggregationLabelStandard = DisaggregationLabel.objects.all().filter(disaggregation_type__standard=True)
        except DisaggregationLabel.DoesNotExist:
            getDisaggregationLabelStandard = None
            getDisaggregationLabel = None

        #set values to None so the form doesn't display empty fields for previous entries
        getDisaggregationValue = None
        indicator = Indicator.objects.get(pk=self.kwargs.get('indicator'))

        context.update({'getDisaggregationValue': getDisaggregationValue})
        context.update({'getDisaggregationLabel': getDisaggregationLabel})
        context.update({'getDisaggregationLabelStandard': getDisaggregationLabelStandard})
        context.update({'indicator_id': self.kwargs['indicator']})
        context.update({'indicator': indicator})
        context.update({'program_id': self.kwargs['program']})

        return context

    def get_initial(self):
        initial = {
            'indicator': self.kwargs['indicator'],
            'program': self.kwargs['program'],
        }

        return initial

    # add the request to the kwargs
    def get_form_kwargs(self):
        kwargs = super(CollectedDataCreate, self).get_form_kwargs()
        kwargs['request'] = self.request
        kwargs['program'] = self.kwargs['program']
        kwargs['indicator'] = self.kwargs['indicator']
        kwargs['tola_table'] = None

        return kwargs


    def form_invalid(self, form):

        messages.error(self.request, 'Invalid Form', fail_silently=False)

        return self.render_to_response(self.get_context_data(form=form))

    def form_valid(self, form):
        disaggregation_labels = DisaggregationLabel.objects.filter(\
                                    Q(disaggregation_type__indicator__id=self.request.POST['indicator']) | \
                                    Q(disaggregation_type__standard=True))

        # update the count with the value of Table unique count
        if form.instance.update_count_tola_table and form.instance.tola_table:
            try:
                getTable = TolaTable.objects.get(id=self.request.POST['tola_table'])
            except DisaggregationLabel.DoesNotExist:
                getTable = None
            if getTable:
                # if there is a trailing slash, remove it since TT api does not like it.
                url = getTable.url if getTable.url[-1:] != "/" else getTable.url[:-1]
                url = url if url[-5:] != "/data" else url[:-5]
                count = getTableCount(url, getTable.table_id)
            else:
                count = 0
            form.instance.achieved = count

        new = form.save()

        process_disaggregation = False

        for label in disaggregation_labels:
            if process_disaggregation == True:
                break
            for k, v in self.request.POST.iteritems():
                if k == str(label.id) and len(v) > 0:
                    process_disaggregation = True
                    break

        if process_disaggregation == True:
            for label in disaggregation_labels:
                for k, v in self.request.POST.iteritems():
                    if k == str(label.id):
                        save = new.disaggregation_value.create(disaggregation_label=label, value=v)
                        new.disaggregation_value.add(save.id)
            process_disaggregation = False


        if self.request.is_ajax():
            data = serializers.serialize('json', [new])
            return HttpResponse(data)

        messages.success(self.request, 'Success, Data Created!')

        redirect_url = '/indicators/home/0/0/0/#hidden-' + str(self.kwargs['program'])
        return HttpResponseRedirect(redirect_url)


class CollectedDataUpdate(UpdateView):
    """
    CollectedData Form
    """
    model = CollectedData
    #template_name = 'indicators/collecteddata_form.html'
    def get_template_names(self):
        if self.request.is_ajax():
            return 'indicators/collecteddata_form_modal.html'
        return 'indicators/collecteddata_form.html'

    @method_decorator(group_excluded('ViewOnly', url='workflow/permission'))
    def dispatch(self, request, *args, **kwargs):
        try:
            self.guidance = FormGuidance.objects.get(form="CollectedData")
        except FormGuidance.DoesNotExist:
            self.guidance = None
        return super(CollectedDataUpdate, self).dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super(CollectedDataUpdate, self).get_context_data(**kwargs)
        #get the indicator_id for the collected data
        getIndicator = CollectedData.objects.get(id=self.kwargs['pk'])

        try:
            getDisaggregationLabel = DisaggregationLabel.objects.all().filter(disaggregation_type__indicator__id=getIndicator.indicator_id)
            getDisaggregationLabelStandard = DisaggregationLabel.objects.all().filter(disaggregation_type__standard=True)
        except DisaggregationLabel.DoesNotExist:
            getDisaggregationLabel = None
            getDisaggregationLabelStandard = None

        try:
            getDisaggregationValue = DisaggregationValue.objects.all().filter(collecteddata=self.kwargs['pk']).exclude(disaggregation_label__disaggregation_type__standard=True)
            getDisaggregationValueStandard = DisaggregationValue.objects.all().filter(collecteddata=self.kwargs['pk']).filter(disaggregation_label__disaggregation_type__standard=True)
        except DisaggregationLabel.DoesNotExist:
            getDisaggregationValue = None
            getDisaggregationValueStandard = None

        context.update({'getDisaggregationLabelStandard': getDisaggregationLabelStandard})
        context.update({'getDisaggregationValueStandard': getDisaggregationValueStandard})
        context.update({'getDisaggregationValue': getDisaggregationValue})
        context.update({'getDisaggregationLabel': getDisaggregationLabel})
        context.update({'id': self.kwargs['pk']})
        context.update({'indicator_id': getIndicator.indicator_id})
        context.update({'indicator': getIndicator})


        return context

    def form_invalid(self, form):
        messages.error(self.request, 'Invalid Form', fail_silently=False)
        return self.render_to_response(self.get_context_data(form=form))

    # add the request to the kwargs
    def get_form_kwargs(self):
        get_data = CollectedData.objects.get(id=self.kwargs['pk'])
        kwargs = super(CollectedDataUpdate, self).get_form_kwargs()
        kwargs['request'] = self.request
        kwargs['program'] = get_data.program
        kwargs['indicator'] = get_data.indicator
        if get_data.tola_table:
            kwargs['tola_table'] = get_data.tola_table.id
        else:
            kwargs['tola_table'] = None
        return kwargs

    def form_valid(self, form):

        getCollectedData = CollectedData.objects.get(id=self.kwargs['pk'])
        getDisaggregationLabel = DisaggregationLabel.objects.all().filter(Q(disaggregation_type__indicator__id=self.request.POST['indicator']) | Q(disaggregation_type__standard=True)).distinct()

        getIndicator = CollectedData.objects.get(id=self.kwargs['pk'])

        # update the count with the value of Table unique count
        if form.instance.update_count_tola_table and form.instance.tola_table:
            try:
                getTable = TolaTable.objects.get(id=self.request.POST['tola_table'])
            except TolaTable.DoesNotExist:
                getTable = None
            if getTable:
                # if there is a trailing slash, remove it since TT api does not like it.
                url = getTable.url if getTable.url[-1:] != "/" else getTable.url[:-1]
                url = url if url[-5:] != "/data" else url[:-5]
                count = getTableCount(url, getTable.table_id)
            else:
                count = 0
            form.instance.achieved = count

        # save the form then update manytomany relationships
        form.save()

        # Insert or update disagg values
        for label in getDisaggregationLabel:
            for key, value in self.request.POST.iteritems():
                if key == str(label.id):
                    value_to_insert = value
                    save = getCollectedData.disaggregation_value.create(disaggregation_label=label, value=value_to_insert)
                    getCollectedData.disaggregation_value.add(save.id)

        if self.request.is_ajax():
            data = serializers.serialize('json', [self.object])
            return HttpResponse(data)

        messages.success(self.request, 'Success, Data Updated!')

        redirect_url = '/indicators/home/0/0/0/#hidden-' + str(getIndicator.program.id)
        return HttpResponseRedirect(redirect_url)

    form_class = CollectedDataForm


class CollectedDataDelete(DeleteView):
    """
    CollectedData Delete
    """
    model = CollectedData
    success_url = '/indicators/home/0/0/0/'

    @method_decorator(group_excluded('ViewOnly', url='workflow/permission'))
    def dispatch(self, request, *args, **kwargs):
        return super(CollectedDataDelete, self).dispatch(request, *args, **kwargs)


def getTableCount(url,table_id):
    """
    Count the number of rowns in a TolaTable
    :param table_id: The TolaTable ID to update count from and return
    :return: count : count of rows from TolaTable
    """
    token = TolaSites.objects.get(site_id=1)
    if token.tola_tables_token:
        headers = {'content-type': 'application/json', 'Authorization': 'Token ' + token.tola_tables_token }
    else:
        headers = {'content-type': 'application/json'}
        print "Token Not Found"

    response = requests.get(url,headers=headers, verify=True)
    data = json.loads(response.content)
    count = None
    try:
        count = data['data_count']
        TolaTable.objects.filter(table_id = table_id).update(unique_count=count)
    except KeyError:
        pass

    return count


def merge_two_dicts(x, y):
    """
    Given two dictionary Items, merge them into a new dict as a shallow copy.
    :param x: Dict 1
    :param y: Dict 2
    :return: Merge of the 2 Dicts
    """
    z = x.copy()
    z.update(y)
    return z


def collecteddata_import(request):
    """
    Import collected data from Tola Tables
    :param request:
    :return:
    """
    owner = request.user
    #get the TolaTables URL and token from the sites object
    service = TolaSites.objects.get(site_id=1)

    # add filter to get just the users tables only
    user_filter_url = service.tola_tables_url + "&owner__username=" + str(owner)
    shared_filter_url = service.tola_tables_url + "&shared__username=" + str(owner)

    user_json = get_table(user_filter_url)
    shared_json = get_table(shared_filter_url)

    if type(shared_json) is not dict:
        data = user_json + shared_json
    else:
        data = user_json

    if request.method == 'POST':
        id = request.POST['service_table']
        filter_url = service.tola_tables_url + "&id=" + id

        data = get_table(filter_url)

        # Get Data Info
        for item in data:
            name = item['name']
            url = item['data']
            remote_owner = item['owner']['username']

        #send table ID to count items in data
        count = getTableCount(url,id)

        # get the users country
        countries = getCountry(request.user)
        check_for_existence = TolaTable.objects.all().filter(name=name,owner=owner)
        if check_for_existence:
            result = check_for_existence[0].id
        else:
            create_table = TolaTable.objects.create(name=name,owner=owner,remote_owner=remote_owner,table_id=id,url=url, unique_count=count)
            create_table.country.add(countries[0].id)
            create_table.save()
            result = create_table.id

        # send result back as json
        message = result
        return HttpResponse(json.dumps(message), content_type='application/json')

    # send the keys and vars from the json data to the template along with submitted feed info and silos for new form
    return render(request, "indicators/collecteddata_import.html", {'getTables': data})


def service_json(request,service):
    """
    For populating service indicators in dropdown
    :param service: The remote data service
    :return: JSON object of the indicators from the service
    """
    service_indicators = import_indicator(service,deserialize=False)
    return HttpResponse(service_indicators, content_type="application/json")


def collected_data_json(AjaxableResponseMixin, indicator, program):
    ind = Indicator.objects.get(pk=indicator)
    template_name = 'indicators/collected_data_table.html'

    # collecteddata = CollectedData.objects\
    #     .filter(indicator=indicator)\
    #     .select_related('indicator')\
    #     .prefetch_related('evidence', 'periodic_target', 'disaggregation_value')\
    #     .order_by('periodic_target__customsort', 'date_collected')

    periodictargets = PeriodicTarget.objects.filter(indicator=indicator).prefetch_related('collecteddata_set').order_by('customsort')
    collecteddata_without_periodictargets = CollectedData.objects.filter(indicator=indicator, periodic_target__isnull=True)

    detail_url = ''
    try:
        for data in collecteddata:
            if data.tola_table:
                data.tola_table.detail_url = const_table_det_url(str(data.tola_table.url))
    except Exception, e:
        pass

    collected_sum = CollectedData.objects\
        .select_related('periodic_target')\
        .filter(indicator=indicator)\
        .aggregate(Sum('periodic_target__target'),Sum('achieved'))

    return render_to_response(template_name, {
                'periodictargets': periodictargets,
                'collecteddata_without_periodictargets': collecteddata_without_periodictargets,
                'collected_sum': collected_sum,
                'indicator': ind,
                'program_id': program})


def program_indicators_json(AjaxableResponseMixin, program, indicator, type):
    template_name = 'indicators/program_indicators_table.html'

    q = {'program__id__isnull': False}
    if int(program) != 0:
        q['program__id'] = program

    if int(type) != 0:
        q['indicator_type__id'] = type

    if int(indicator) != 0:
        q['id'] = indicator

        #
    indicators = Indicator.objects\
        .select_related('sector')\
        .prefetch_related('collecteddata_set', 'indicator_type', 'level', 'periodictarget_set')\
        .filter(**q)\
        .annotate(data_count=Count('collecteddata'), levelmin=Min('level__id'))\
        .order_by('levelmin', 'number')

    return render_to_response(template_name, {'indicators': indicators, 'program_id': program})


def tool(request):
    """
    Placeholder for Indicator planning Tool TBD
    :param request:
    :return:
    """
    return render(request, 'indicators/tool.html')


# REPORT VIEWS
def indicator_report(request, program=0, indicator=0, type=0):
    countries = request.user.tola_user.countries.all()
    getPrograms = Program.objects.filter(funding_status="Funded", country__in=countries).distinct()
    getIndicatorTypes = IndicatorType.objects.all()

    filters = {}
    if int(program) != 0:
        filters['program__id'] = program
    if int(type) != 0:
        filters['indicator_type'] = type
    if int(indicator) != 0:
        filters['id'] = indicator

    filters['program__country__in'] = countries

    indicator_data = Indicator.objects.filter(**filters)\
            .prefetch_related('sector')\
            .select_related('program', 'external_service_record','indicator_type',\
                'disaggregation', 'reporting_frequency')\
            .values('id','program__name','baseline','level__name','lop_target',\
                   'program__id','external_service_record__external_service__name',\
                   'key_performance_indicator','name','indicator_type__id', 'indicator_type__indicator_type',\
                   'sector__sector','disaggregation__disaggregation_type',\
                   'means_of_verification','data_collection_method',\
                   'reporting_frequency__frequency','create_date','edit_date',\
                   'source','method_of_analysis')

    data = json.dumps(list(indicator_data), cls=DjangoJSONEncoder)

    # send the keys and vars from the json data to the template along with submitted feed info and silos for new form
    return render(request, "indicators/report.html", {
                  'program': program,
                  'getPrograms': getPrograms,
                  'getIndicatorTypes': getIndicatorTypes,
                  'getIndicators': indicator_data,
                  'data': data})


class IndicatorReport(View, AjaxableResponseMixin):
    def get(self, request, *args, **kwargs):

        countries = getCountry(request.user)
        getPrograms = Program.objects.all().filter(funding_status="Funded", country__in=countries).distinct()

        getIndicatorTypes = IndicatorType.objects.all()

        program = int(self.kwargs['program'])
        indicator = int(self.kwargs['indicator'])
        type = int(self.kwargs['type'])

        filters = {}
        if program != 0:
            filters['program__id'] = program
        if type != 0:
            filters['indicator_type'] = type
        if indicator != 0:
            filters['id'] = indicator
        if program == 0 and type == 0:
            filters['program__country__in'] = countries

        getIndicators = Indicator.objects.filter(**filters)\
            .prefetch_related('sector')\
            .select_related('program', 'external_service_record','indicator_type',\
                'disaggregation', 'reporting_frequency')\
            .values('id','program__name','baseline','level__name','lop_target',\
                   'program__id','external_service_record__external_service__name',\
                   'key_performance_indicator','name','indicator_type__indicator_type',\
                   'sector__sector','disaggregation__disaggregation_type',\
                   'means_of_verification','data_collection_method',\
                   'reporting_frequency__frequency','create_date','edit_date',\
                   'source','method_of_analysis')


        q = request.GET.get('search', None)
        if q:
            getIndicators = getIndicators.filter(
                Q(indicator_type__indicator_type__contains=q) |
                Q(name__contains=q) |
                Q(number__contains=q) |
                Q(number__contains=q) |
                Q(sector__sector__contains=q) |
                Q(definition__contains=q)
            )

        get_indicators = json.dumps(list(getIndicators), cls=DjangoJSONEncoder)

        return JsonResponse(get_indicators, safe=False)


def programIndicatorReport(request, program=0):
    """
    This is the GRID report or indicator plan for a program.  Shows a simple list of indicators sorted by level
    and number. Lives in the "Indicator" home page as a link.
    URL: indicators/program_report/[program_id]/
    :param request:
    :param program:
    :return:
    """
    program = int(program)
    countries = getCountry(request.user)
    getPrograms = Program.objects.all().filter(funding_status="Funded", country__in=countries).distinct()
    getIndicators = Indicator.objects.all().filter(program__id=program).select_related().order_by('level', 'number')
    getProgram = Program.objects.get(id=program)

    getIndicatorTypes = IndicatorType.objects.all()

    if request.method == "GET" and "search" in request.GET:
        # list1 = list()
        # for obj in filtered:
        #    list1.append(obj)
        getIndicators = Indicator.objects.all().filter(
            Q(indicator_type__icontains=request.GET["search"]) |
            Q(name__icontains=request.GET["search"]) |
            Q(number__icontains=request.GET["search"]) |
            Q(definition__startswith=request.GET["search"])
        ).filter(program__id=program).select_related().order_by('level', 'number')

    # send the keys and vars from the json data to the template along with submitted feed info and silos for new form
    return render(request, "indicators/grid_report.html", {'getIndicators': getIndicators, 'getPrograms': getPrograms,
                                                           'getProgram': getProgram, 'form': FilterForm(),
                                                           'helper': FilterForm.helper,
                                                           'getIndicatorTypes': getIndicatorTypes})


def indicator_data_report(request, id=0, program=0, type=0):
    countries = request.user.tola_user.countries.all()
    getPrograms = Program.objects.all().filter(funding_status="Funded", country__in=countries).distinct()
    getIndicators = Indicator.objects.select_related().filter(program__country__in=countries)
    getIndicatorTypes = IndicatorType.objects.all()
    indicator_name = None
    program_name = None
    type_name = None
    q = {'indicator__id__isnull': False}

    getSiteProfile = SiteProfile.objects\
        .filter(projectagreement__program__country__in=countries)\
        .select_related('country','district','province')

    if int(id) != 0:
        indicator_name = Indicator.objects.get(id=id).name
        q['indicator__id'] = id
    else:
        q['indicator__program__country__in'] = countries

    if int(program) != 0:
        getSiteProfile = SiteProfile.objects\
            .filter(projectagreement__program__id=program)\
            .select_related('country','district','province')
        program_name = Program.objects.get(id=program).name
        q = {'program__id': program}
        getIndicators = Indicator.objects.select_related().filter(program=program)

    if int(type) != 0:
        type_name = IndicatorType.objects.get(id=type).indicator_type
        q = {'indicator__indicator_type__id': type}

    if request.method == "GET" and "search" in request.GET:
        queryset = CollectedData.objects.filter(**q)\
            .filter(Q(agreement__project_name__contains=request.GET["search"]) |
                Q(description__icontains=request.GET["search"]) |
                Q(indicator__name__contains=request.GET["search"]))\
            .select_related()
    else:
        queryset = CollectedData.objects.all().filter(**q).select_related()

    # pass query to table and configure
    table = IndicatorDataTable(queryset)
    table.paginate(page=request.GET.get('page', 1), per_page=20)

    RequestConfig(request).configure(table)

    filters = {'status': 1, 'country__in': countries}
    getSiteProfileIndicator = SiteProfile.objects\
        .select_related('country','district','province')\
        .prefetch_related('collecteddata_set')\
        .filter(**filters)

    # send the keys and vars from the json data to the template along with submitted feed info and silos for new form
    return render(request, "indicators/data_report.html", {
        'getQuantitativeData': queryset,
        'countries': countries,
        'getSiteProfile': getSiteProfile,
        'getPrograms': getPrograms,
        'getIndicators': getIndicators,
        'getIndicatorTypes': getIndicatorTypes,
        'form': FilterForm(),
        'helper': FilterForm.helper,
        'id': id,
        'program': program,
        'type': type,
        'indicator': id,
        'indicator_name': indicator_name,
        'type_name': type_name,
        'program_name': program_name,
        'getSiteProfileIndicator': getSiteProfileIndicator,
        })


class IndicatorReportData(View, AjaxableResponseMixin):
    """
    This is the Indicator Visual report data, returns a json object of report data to be displayed in the table report
    URL: indicators/report_data/[id]/[program]/
    :param request:
    :param id: Indicator ID
    :param program: Program ID
    :param type: Type ID
    :return: json dataset
    """

    def get(self, request, program, type, id):
        q = {'program__id__isnull': False}
        # if we have a program filter active
        if int(program) != 0:
            q = {
                'program__id': program,
            }
        # if we have an indicator type active
        if int(type) != 0:
            r = {
                'indicator_type__id': type,
            }
            q.update(r)
        # if we have an indicator id append it to the query filter
        if int(id) != 0:
            s = {
                'id': id,
            }
            q.update(s)

        countries = getCountry(request.user)

        indicator = Indicator.objects.filter(program__country__in=countries).filter(**q).values(\
            'id', 'program__name', 'baseline','level__name','lop_target','program__id',\
            'external_service_record__external_service__name', 'key_performance_indicator',\
            'name','indicator_type__id', 'indicator_type__indicator_type', \
            'sector__sector').order_by('create_date')

        #indicator = {x['id']:x for x in indcator}.values()

        indicator_count = Indicator.objects.all().filter(program__country__in=countries).filter(**q).filter(
            collecteddata__isnull=True).distinct().count()
        indicator_data_count = Indicator.objects.all().filter(program__country__in=countries).filter(**q).filter(collecteddata__isnull=False).distinct().count()

        indicator_serialized = json.dumps(list(indicator))

        final_dict = {
            'indicator': indicator_serialized,
            'indicator_count': indicator_count,
            'data_count': indicator_data_count
        }

        if request.GET.get('export'):
            indicator_export = Indicator.objects.all().filter(**q)
            dataset = IndicatorResource().export(indicator_export)
            response = HttpResponse(dataset.csv, content_type='application/ms-excel')
            response['Content-Disposition'] = 'attachment; filename=indicator_data.csv'
            return response

        return JsonResponse(final_dict, safe=False)


class CollectedDataReportData(View, AjaxableResponseMixin):
    """
    This is the Collected Data reports data in JSON format for a specific indicator
    URL: indicators/collectedaata/[id]/
    :param request:
    :param indicator: Indicator ID
    :return: json dataset
    """

    def get(self, request, *args, **kwargs):

        countries = getCountry(request.user)
        program = kwargs['program']
        indicator = kwargs['indicator']
        type = kwargs['type']

        q = {'program__id__isnull': False}
        # if we have a program filter active
        if int(program) != 0:
            q = {
                'indicator__program__id': program,
            }
        # if we have an indicator type active
        if int(type) != 0:
            r = {
                'indicator__indicator_type__id': type,
            }
            q.update(r)
        # if we have an indicator id append it to the query filter
        if int(indicator) != 0:
            s = {
                'indicator__id': indicator,
            }
            q.update(s)

        getCollectedData = CollectedData.objects.all().select_related('periodic_target').prefetch_related('evidence', 'indicator', 'program',
                                                                        'indicator__objectives',
                                                                        'indicator__strategic_objectives').filter(
            program__country__in=countries).filter(
            **q).order_by(
            'indicator__program__name',
            'indicator__number').values('id', 'indicator__id', 'indicator__name', 'indicator__program__id', 'indicator__program__name',
                                        'indicator__indicator_type__indicator_type', 'indicator__indicator_type__id', 'indicator__level__name',
                                        'indicator__sector__sector', 'date_collected', 'indicator__baseline',
                                        'indicator__lop_target', 'indicator__key_performance_indicator',
                                        'indicator__external_service_record__external_service__name', 'evidence',
                                        'tola_table', 'periodic_target', 'achieved')

        #getCollectedData = {x['id']:x for x in getCollectedData}.values()

        collected_sum = CollectedData.objects.select_related('periodic_target').filter(program__country__in=countries).filter(**q).aggregate(
            Sum('periodic_target__target'), Sum('achieved'))

        # datetime encoding breaks without using this
        from django.core.serializers.json import DjangoJSONEncoder
        collected_serialized = json.dumps(list(getCollectedData), cls=DjangoJSONEncoder)

        final_dict = {
            'collected': collected_serialized,
            'collected_sum': collected_sum
        }

        return JsonResponse(final_dict, safe=False)

def dictfetchall(cursor):
    "Return all rows from a cursor as a dict"
    columns = [col[0] for col in cursor.description]
    return [
        dict(zip(columns, row))
        for row in cursor.fetchall()
    ]


class DisaggregationReportMixin(object):
    def get_context_data(self, **kwargs):
        context = super(DisaggregationReportMixin, self).get_context_data(**kwargs)

        countries = getCountry(self.request.user)
        programs = Program.objects.filter(funding_status="Funded", country__in=countries).distinct()
        indicators = Indicator.objects.filter(program__country__in=countries)

        programId = int(kwargs.get('program', 0))
        program_selected = None
        if programId:
            program_selected = Program.objects.filter(id=programId).first()
            if program_selected.indicator_set.count() > 0:
                indicators = indicators.filter(program=programId)

        disagg_query = "SELECT i.id AS IndicatorID, dt.disaggregation_type AS DType, "\
            "l.customsort AS customsort, l.label AS Disaggregation, SUM(dv.value) AS Actuals "\
                "FROM indicators_collecteddata_disaggregation_value AS cdv "\
                "INNER JOIN indicators_collecteddata AS c ON c.id = cdv.collecteddata_id "\
                "INNER JOIN indicators_indicator AS i ON i.id = c.indicator_id "\
                "INNER JOIN indicators_indicator_program AS ip ON ip.indicator_id = i.id "\
                "INNER JOIN workflow_program AS p ON p.id = ip.program_id "\
                "INNER JOIN indicators_disaggregationvalue AS dv ON dv.id = cdv.disaggregationvalue_id "\
                "INNER JOIN indicators_disaggregationlabel AS l ON l.id = dv.disaggregation_label_id "\
                "INNER JOIN indicators_disaggregationtype AS dt ON dt.id = l.disaggregation_type_id "\
                "WHERE p.id = %s "\
                "GROUP BY IndicatorID, DType, customsort, Disaggregation "\
                "ORDER BY IndicatorID, DType, customsort, Disaggregation;"  % programId
        cursor = connection.cursor()
        cursor.execute(disagg_query)
        disdata = dictfetchall(cursor)


        indicator_query = "SELECT DISTINCT p.id as PID, i.id AS IndicatorID, i.number AS INumber, i.name AS Indicator, "\
            "i.lop_target AS LOP_Target, SUM(cd.achieved) AS Overall "\
            "FROM indicators_indicator AS i "\
            "INNER JOIN indicators_indicator_program AS ip ON ip.indicator_id = i.id "\
            "INNER JOIN workflow_program AS p ON p.id = ip.program_id "\
            "LEFT OUTER JOIN indicators_collecteddata AS cd ON i.id = cd.indicator_id "\
            "WHERE p.id = %s "\
            "GROUP BY PID, IndicatorID "\
            "ORDER BY Indicator; " % programId
        cursor.execute(indicator_query)
        idata = dictfetchall(cursor)

        for indicator in idata:
            indicator["disdata"] = []
            for i, dis in enumerate(disdata):
                if dis['IndicatorID'] == indicator['IndicatorID']:
                    indicator["disdata"].append(disdata[i])

        context['program_id'] = programId
        context['data'] = idata
        context['getPrograms'] = programs
        context['getIndicators'] = indicators
        context['program_selected'] = program_selected
        return context

class DisaggregationReport(DisaggregationReportMixin, TemplateView):
    template_name = 'indicators/disaggregation_report.html'

    def get_context_data(self, **kwargs):
        context = super(DisaggregationReport, self).get_context_data(**kwargs)
        context['disaggregationprint_button'] = True
        return context


class DisaggregationPrint(DisaggregationReportMixin, TemplateView):
    template_name = 'indicators/disaggregation_print.html'


    def get(self, request, *args, **kwargs):
        context = super(DisaggregationPrint, self).get_context_data(**kwargs)
        hmtl_string = render(request, self.template_name, {'data': context['data'], 'program_selected': context['program_selected']})
        pdffile = HTML(string=hmtl_string.content)

        result = pdffile.write_pdf(stylesheets=[CSS(
            string='@page {\
                size: letter; margin: 1cm;\
                @bottom-right{\
                    content: "Page " counter(page) " of " counter(pages);\
                };\
            }'\
        )])
        res = HttpResponse(result, content_type='application/pdf')
        res['Content-Disposition'] = 'attachment; filename=indicators_disaggregation_report.pdf'
        res['Content-Transfer-Encoding'] = 'binary'
        #return super(DisaggregationReport, self).get(request, *args, **kwargs)
        return res

from django.template.loader import render_to_string
#import tempfile

class TVAPrint(TemplateView):
    template_name = 'indicators/tva_print.html'

    def get(self, request, *args, **kwargs):
        program = Program.objects.filter(id=kwargs.get('program', None)).first()
        indicators = Indicator.objects\
            .select_related('sector')\
            .prefetch_related('indicator_type', 'level', 'program')\
            .filter(program=program)\
            .annotate(actuals=Sum('collecteddata__achieved'))

        #hmtl_string = render_to_string('indicators/tva_print.html', {'data': context['data'], 'program': context['program']})
        hmtl_string = render(request, 'indicators/tva_print.html', {'data': indicators, 'program': program})
        pdffile = HTML(string=hmtl_string.content)
        # stylesheets=[CSS(string='@page { size: letter; margin: 1cm}')]
        result = pdffile.write_pdf(stylesheets=[CSS(
            string='@page {\
                size: letter; margin: 1cm;\
                @bottom-right{\
                    content: "Page " counter(page) " of " counter(pages);\
                };\
            }'\
        )])
        res = HttpResponse(result, content_type='application/pdf')
        #res['Content-Disposition'] = 'inline; filename="ztvareport.pdf"'
        res['Content-Disposition'] = 'attachment; filename=tva.pdf'
        res['Content-Transfer-Encoding'] = 'binary'
        """
        with tempfile.NamedTemporaryFile(delete=True) as output:
            output.write(result)
            output.flush()
            output = open(output.name, 'r')
            res.write(output.read())
        """
        """
        # Create the PDF object, using the response object as its "file."
        p = canvas.Canvas(res)
        p.drawString(100, 100, 'hello world!')
        p.showPage()
        p.save()
        """
        return res

class TVAReport(TemplateView):
    template_name = 'indicators/tva_report.html'

    def get_context_data(self, **kwargs):
        context = super(TVAReport, self).get_context_data(**kwargs)
        countries = getCountry(self.request.user)
        filters = {'program__country__in': countries}
        program = Program.objects.filter(id=kwargs.get('program', None)).first()
        indicator_type = IndicatorType.objects.filter(id=kwargs.get('type', None)).first()
        indicator = Indicator.objects.filter(id=kwargs.get('indicator', None)).first()

        if program:
            filters['program'] = program.pk
        if indicator_type:
            filters['indicator__indicator_type__id'] = indicator_type.pk
        if indicator:
            filters['indicator'] = indicator.pk

        indicators = Indicator.objects\
            .select_related('sector')\
            .prefetch_related('indicator_type', 'level', 'program')\
            .filter(**filters)\
            .annotate(actuals=Sum('collecteddata__achieved'))
            #.annotate(actuals=Sum('collecteddata__disaggregation_value__value'))
        context['data'] = indicators
        context['getIndicators'] = Indicator.objects.filter(program__country__in=countries).exclude(collecteddata__isnull=True)
        context['getPrograms'] = Program.objects.filter(funding_status="Funded", country__in=countries).distinct()
        context['getIndicatorTypes'] = IndicatorType.objects.all()
        context['program'] = program
        context['export_to_pdf_url'] = True
        return context



class CollectedDataList(ListView):
    """
    This is the Indicator CollectedData report for each indicator and program.  Displays a list collected data entries
    and sums it at the bottom.  Lives in the "Reports" navigation.
    URL: indicators/data/[id]/[program]/[type]
    :param request:
    :param indicator: Indicator ID
    :param program: Program ID
    :param type: Type ID
    :return:
    """
    model = CollectedData
    template_name = 'indicators/collecteddata_list.html'

    def get(self, request, *args, **kwargs):

        countries = getCountry(request.user)
        getPrograms = Program.objects.all().filter(funding_status="Funded", country__in=countries).distinct()
        getIndicators = Indicator.objects.all().filter(program__country__in=countries).exclude(
            collecteddata__isnull=True)
        getIndicatorTypes = IndicatorType.objects.all()
        program = self.kwargs['program']
        indicator = self.kwargs['indicator']
        type = self.kwargs['type']
        indicator_name = ""
        type_name = ""
        program_name = ""

        q = {'program__id__isnull': False}
        # if we have a program filter active
        if int(program) != 0:
            q = {
                'program__id': program,
            }
            # redress the indicator list based on program
            getIndicators = Indicator.objects.select_related().filter(program=program)
            program_name = Program.objects.get(id=program)
        # if we have an indicator type active
        if int(type) != 0:
            r = {
                'indicator__indicator_type__id': type,
            }
            q.update(r)
            # redress the indicator list based on type
            getIndicators = Indicator.objects.select_related().filter(indicator_type__id=type)
            type_name = IndicatorType.objects.get(id=type).indicator_type
        # if we have an indicator id append it to the query filter
        if int(indicator) != 0:
            s = {
                'indicator': indicator,
            }
            q.update(s)
            indicator_name = Indicator.objects.get(id=indicator)

        indicators = CollectedData.objects.all().select_related('periodic_target').prefetch_related('evidence', 'indicator', 'program',
                                                                  'indicator__objectives',
                                                                  'indicator__strategic_objectives').filter(
            program__country__in=countries).filter(
            **q).order_by(
            'indicator__program__name',
            'indicator__number').values('indicator__id', 'indicator__name', 'indicator__program__name',
                                        'indicator__indicator_type__indicator_type', 'indicator__level__name',
                                        'indicator__sector__sector', 'date_collected', 'indicator__baseline',
                                        'indicator__lop_target', 'indicator__key_performance_indicator',
                                        'indicator__external_service_record__external_service__name', 'evidence',
                                        'tola_table', 'periodic_target', 'achieved')

        if self.request.GET.get('export'):
            dataset = CollectedDataResource().export(indicators)
            response = HttpResponse(dataset.csv, content_type='application/ms-excel')
            response['Content-Disposition'] = 'attachment; filename=indicator_data.csv'
            return response

        return render(request, self.template_name, {
                    'indicators': indicators,
                    'getPrograms': getPrograms,
                    'getIndicatorTypes': getIndicatorTypes,
                    'getIndicators': getIndicators,
                    'program': program,
                    'indicator': indicator,
                    'type': type,
                    'filter_program': program_name,
                    'filter_indicator': indicator_name,
                    'indicator': indicator,
                    'program': program,
                    'indicator_name': indicator_name,
                    'program_name': program_name,
                    'type_name': type_name,
                    })


class IndicatorExport(View):
    """
    Export all indicators to a CSV file
    """
    def get(self, request, *args, **kwargs ):


        if int(kwargs['id']) == 0:
            del kwargs['id']
        if int(kwargs['indicator_type']) == 0:
            del kwargs['indicator_type']
        if int(kwargs['program']) == 0:
            del kwargs['program']

        countries = getCountry(request.user)

        queryset = Indicator.objects.filter(**kwargs).filter(program__country__in=countries)


        indicator = IndicatorResource().export(queryset)
        response = HttpResponse(indicator.csv, content_type='application/ms-excel')
        response['Content-Disposition'] = 'attachment; filename=indicator.csv'
        return response


class IndicatorDataExport(View):
    """
    Export all indicators to a CSV file
    """
    def get(self, request, *args, **kwargs ):

        if int(kwargs['indicator']) == 0:
            del kwargs['indicator']
        if int(kwargs['program']) == 0:
            del kwargs['program']
        if int(kwargs['type']) == 0:
            del kwargs['type']
        else:
           kwargs['indicator__indicator_type__id'] = kwargs['type']
           del kwargs['type']

        countries = getCountry(request.user)

        queryset = CollectedData.objects.filter(**kwargs).filter(indicator__program__country__in=countries)
        dataset = CollectedDataResource().export(queryset)
        response = HttpResponse(dataset.csv, content_type='application/ms-excel')
        response['Content-Disposition'] = 'attachment; filename=indicator_data.csv'
        return response


class CountryExport(View):

    def get(self, *args, **kwargs ):
        country = CountryResource().export()
        response = HttpResponse(country.csv, content_type="csv")
        response['Content-Disposition'] = 'attachment; filename=country.csv'
        return response

def const_table_det_url(url):
    url_data = urlparse(url)
    root = url_data.scheme
    org_host = url_data.netloc
    path = url_data.path
    components = re.split('/', path)

    s = []
    for c in components:
        s.append(c)

    new_url = str(root)+'://'+str(org_host)+'/silo_detail/'+str(s[3])+'/'

    return new_url
