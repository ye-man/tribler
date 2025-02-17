"""
This file contains various controllers for table views.
The responsibility of the controller is to populate the table view with some data, contained in a specific model.
"""
from __future__ import absolute_import

import uuid

from PyQt5.QtCore import QObject, Qt, pyqtSignal
from PyQt5.QtGui import QCursor
from PyQt5.QtWidgets import QAction

from six import text_type

from TriblerGUI.tribler_action_menu import TriblerActionMenu
from TriblerGUI.tribler_request_manager import TriblerRequestManager


def sanitize_for_fts(text):
    return text_type(text).translate({ord(u"\""): u"\"\"", ord(u"\'"): u"\'\'"})


def to_fts_query(text):
    if not text:
        return ""
    words = text.split(" ")

    # TODO: add support for quoted exact searches
    query_list = [u'\"' + sanitize_for_fts(word) + u'\"*' for word in words]

    return " AND ".join(query_list)


class TriblerTableViewController(QObject):
    """
    Base controller for a table view that displays some data.
    """
    query_complete = pyqtSignal(dict, bool)

    def __init__(self, model, table_view):
        super(TriblerTableViewController, self).__init__()
        self.model = model
        self.model.on_sort.connect(self._on_view_sort)
        self.table_view = table_view
        self.table_view.setModel(self.model)
        self.table_view.verticalScrollBar().valueChanged.connect(self._on_list_scroll)
        self.query_text = ''
        self.num_results_label = None
        self.request_mgr = None
        self.query_uuid = None

    def _on_view_sort(self, column, ascending):
        self.model.reset()
        self.perform_query(first=1, last=50)

    def _on_list_scroll(self, event):
        if self.table_view.verticalScrollBar().value() == self.table_view.verticalScrollBar().maximum() and \
                self.model.data_items:  # workaround for duplicate calls to _on_list_scroll on view creation
            self.perform_query()

    def _get_sort_parameters(self):
        """
        Return a tuple (column_name, sort_asc) that indicates the sorting column/order of the table view.
        """
        sort_by = self.model.columns[self.table_view.horizontalHeader().sortIndicatorSection()]
        sort_asc = self.table_view.horizontalHeader().sortIndicatorOrder()
        return sort_by, sort_asc

    def perform_query(self, **kwargs):
        """
        Fetch results for a given query.
        """
        if 'first' not in kwargs or 'last' not in kwargs:
            kwargs["first"], kwargs[
                'last'] = self.model.rowCount() + 1, self.model.rowCount() + self.model.item_load_batch

        # Create a new uuid for each new search
        if kwargs['first'] == 1 or not self.query_uuid:
            self.query_uuid = uuid.uuid4().hex

        sort_by, sort_asc = self._get_sort_parameters()
        kwargs.update({
            "uuid": self.query_uuid,
            "filter": to_fts_query(kwargs.pop('query_filter') if 'query_filter' in kwargs else self.query_text),
            "sort_by": sort_by,
            "sort_asc": sort_asc,
            "hide_xxx": self.model.hide_xxx})

        rest_endpoint_url = kwargs.pop("rest_endpoint_url")
        self.request_mgr = TriblerRequestManager()
        self.request_mgr.perform_request(rest_endpoint_url,
                                         self.on_query_results,
                                         url_params=kwargs)

    def on_query_results(self, response, remote=False):
        """
        Updates the table with the response.
        :param response: List of the items to be added to the model
        :param remote: True if response is from a remote peer. Default: False
        :return: None
        """
        if not response:
            return False
        if self.is_new_result(response):
            self.model.add_items(response['results'], remote=remote)

        # TODO: count remote results
        if not remote:
            self.model.total_items = response['total']
            if self.num_results_label:
                self.num_results_label.setText("%d results" % self.model.total_items)
        self.query_complete.emit(response, remote)
        return True

    def is_new_result(self, response):
        """
        Returns True if the response is a new fresh response else False.
        - If UUID of the response and the last query does not match, then it is a stale response.
        :param response: List of items
        :return: True for fresh response else False
        """
        if self.query_uuid and 'uuid' in response and response['uuid'] != self.query_uuid:
            return False
        return True


class FilterInputMixin(object):

    def _on_filter_input_change(self, _):
        self.query_text = self.filter_input.text().lower()
        self.model.reset()
        self.perform_query(start=1, end=50)


class TableSelectionMixin(object):

    def _on_selection_changed(self, _):
        selected_indices = self.table_view.selectedIndexes()
        if not selected_indices:
            self.details_container.hide()
            self.table_view.clearSelection()
            return

        torrent_info = selected_indices[0].model().data_items[selected_indices[0].row()]
        if 'type' in torrent_info and torrent_info['type'] == 'channel':
            self.details_container.hide()
            return

        first_show = False
        if self.details_container.isHidden():
            first_show = True

        self.details_container.show()
        self.details_container.details_tab_widget.update_with_torrent(selected_indices[0], torrent_info)
        if first_show:
            window = self.table_view.window()
            # FIXME! Brain-dead way to show the rows covered by a newly-opened details_container
            # Note that none of then more civilized ways to fix it work:
            # various updateGeometry, viewport().update, adjustSize - nothing works!
            window.resize(window.geometry().width() + 1, window.geometry().height())
            window.resize(window.geometry().width() - 1, window.geometry().height())


class ContextMenuMixin(object):

    table_view = None

    def enable_context_menu(self, widget):
        self.table_view = widget
        self.table_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table_view.customContextMenuRequested.connect(self._show_context_menu)

    def _show_context_menu(self, pos):
        if not self.table_view:
            return

        item_index = self.table_view.indexAt(pos)
        if not item_index or item_index.row() < 0:
            return

        menu = TriblerActionMenu(self.table_view)

        # Single selection menu items
        num_selected = len(self.table_view.selectionModel().selectedRows())
        if num_selected == 1:
            self.add_menu_item(menu, ' Download ', item_index, self.table_view.on_download_button_clicked)
            self.add_menu_item(menu, ' Play ', item_index, self.table_view.on_play_button_clicked)

        if not isinstance(self, MyTorrentsTableViewController):
            if self.selection_has_torrents():
                self.add_menu_item(menu, ' Add to My Channel ', item_index,
                                   self.table_view.on_add_to_channel_button_clicked)
        else:
            self.add_menu_item(menu, ' Remove from My Channel ', item_index, self.table_view.on_delete_button_clicked)

        menu.exec_(QCursor.pos())

    def add_menu_item(self, menu, name, item_index, callback):
        action = QAction(name, self.table_view)
        action.triggered.connect(lambda _: callback(item_index))
        menu.addAction(action)

    def selection_has_torrents(self):
        for row in self.table_view.selectionModel().selectedRows():
            if row.model().is_torrent_item(row.row()):
                return True
        return False

    def selection_has_channels(self):
        for row in self.table_view.selectionModel().selectedRows():
            if row.model().is_channel_item(row.row()):
                return True
        return False


class SearchResultsTableViewController(TableSelectionMixin, ContextMenuMixin, TriblerTableViewController):
    """
    Controller for the table view that handles search results.
    """

    def __init__(self, model, table_view, details_container, num_results_label=None):
        TriblerTableViewController.__init__(self, model, table_view)
        self.num_results_label = num_results_label
        self.details_container = details_container
        table_view.selectionModel().selectionChanged.connect(self._on_selection_changed)
        self.enable_context_menu(self.table_view)

    def perform_query(self, **kwargs):
        """
        Fetch search results.
        """
        if "rest_endpoint_url" not in kwargs:
            kwargs.update({"metadata_type": self.model.type_filter})
        kwargs.update({"rest_endpoint_url": "search"})
        super(SearchResultsTableViewController, self).perform_query(**kwargs)


class ChannelsTableViewController(TableSelectionMixin, FilterInputMixin, TriblerTableViewController):
    """
    This class manages a list with channels.
    """

    def __init__(self, model, table_view, num_results_label=None, filter_input=None):
        TriblerTableViewController.__init__(self, model, table_view)
        self.num_results_label = num_results_label
        self.filter_input = filter_input

        if self.filter_input:
            self.filter_input.textChanged.connect(self._on_filter_input_change)

    def perform_query(self, **kwargs):
        self.query_text = (self.filter_input.text().lower()
                           if (self.filter_input and self.filter_input.text().lower())
                           else '')
        if "rest_endpoint_url" not in kwargs:
            kwargs.update({"rest_endpoint_url": "metadata/channels"})
        kwargs.update({"subscribed": self.model.subscribed})
        super(ChannelsTableViewController, self).perform_query(**kwargs)


class TorrentsTableViewController(TableSelectionMixin, FilterInputMixin, ContextMenuMixin, TriblerTableViewController):
    """
    This class manages a list with torrents.
    """

    def __init__(self, model, table_view, details_container, num_results_label=None, filter_input=None):
        TriblerTableViewController.__init__(self, model, table_view)
        self.num_results_label = num_results_label
        self.filter_input = filter_input
        self.details_container = details_container
        table_view.selectionModel().selectionChanged.connect(self._on_selection_changed)
        if self.filter_input:
            self.filter_input.textChanged.connect(self._on_filter_input_change)
        self.enable_context_menu(self.table_view)

    def perform_query(self, **kwargs):
        if "rest_endpoint_url" not in kwargs:
            kwargs.update({
                "rest_endpoint_url": "metadata/channels/%s/%i/torrents" % (self.model.channel_pk,
                                                                           self.model.channel_id)})
        super(TorrentsTableViewController, self).perform_query(**kwargs)

    def fetch_preview(self):
        params = {'query_filter': self.model.channel_pk,
                  'metadata_type': 'torrent',
                  'rest_endpoint_url': 'search',
                  'first': 1,
                  'last': 50}
        super(TorrentsTableViewController, self).perform_query(**params)


class MyTorrentsTableViewController(TorrentsTableViewController):
    """
    This class manages the list with the torrents in your own channel.
    """

    def __init__(self, *args, **kwargs):
        super(MyTorrentsTableViewController, self).__init__(*args, **kwargs)
        self.model.row_edited.connect(self._on_row_edited)

    def _on_row_edited(self, index, new_value):
        infohash = self.model.data_items[index.row()][u'infohash']
        attribute_name = self.model.columns[index.column()]
        attribute_name = u'tags' if attribute_name == u'category' else attribute_name
        attribute_name = u'title' if attribute_name == u'name' else attribute_name

        self.request_mgr = TriblerRequestManager()
        self.request_mgr.perform_request(
            "mychannel/torrents/%s" % infohash,
            self._on_row_update_results,
            method='PATCH',
            data={attribute_name: new_value})

    def _on_row_update_results(self, response):
        if response:
            self.table_view.window().edit_channel_page.channel_dirty = response['dirty']
            self.table_view.window().edit_channel_page.update_channel_commit_views()

    def perform_query(self, **kwargs):
        kwargs.update({
            "rest_endpoint_url": "mychannel/torrents",
            "exclude_deleted": self.model.exclude_deleted})
        super(MyTorrentsTableViewController, self).perform_query(**kwargs)

    def on_query_results(self, response):
        if super(MyTorrentsTableViewController, self).on_query_results(response):
            self.table_view.window().edit_channel_page.channel_dirty = response['dirty']
            self.table_view.window().edit_channel_page.update_channel_commit_views()
